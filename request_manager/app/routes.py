from weakref import ref
from app import app, db
from app.utils import check_sla_breach
from app.utils import generate_sla_report
from flask import render_template, request, redirect, url_for, session, jsonify, send_file, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from app.models import db, WarehouseRequest, TicketReply, ProductionRequest, SupportTicket, Inventory, User, Request, Resource, Product, Customer
import pandas as pd
from io import BytesIO
from sqlalchemy import func
from uuid import uuid4

TAG_KEYWORDS = {
    "Stock Inquiry": ["available stock", "inventory", "check stock"],
    "Urgent Delivery": ["urgent", "fast shipping", "priority order"],
    "Shipment Confirmation": ["shipment", "tracking", "dispatch"],
    "Production Delay": ["delay", "raw materials", "out of stock"],
    "Complaint": ["issue", "problem", "not working"]
}

# --------------------------------------------------------------------------------------------
# LOGIN / LOGOUT
# --------------------------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template("login.html")  # Serve login page

    data = request.get_json()  # For AJAX login

    if not data or 'email' not in data or 'password' not in data:
        return jsonify({"error": "Invalid request format"}), 400

    user = User.query.filter_by(email=data['email']).first()

    if not user or not check_password_hash(user.password, data['password']):
        return jsonify({"error": "Invalid credentials"}), 401

    login_user(user)
    session.permanent = True  # Optional: keep session alive longer

    role_map = {
        "@sales.com": "Sales",
        "@warehouse.com": "Warehouse",
        "@production.com": "Production",
        "@support.com": "Support",
    }
    session['role'] = next((role_map[key] for key in role_map if key in user.email), "Unknown")

    # Support Flask-Login's ?next=... redirect
    next_page = request.args.get('next')
    if next_page:
        dashboard_route = next_page
    elif session['role'] == "Sales":
        dashboard_route = "/sales/dashboard"
    elif session['role'] == "Warehouse":
        dashboard_route = "/warehouse/dashboard"
    elif session['role'] == "Production":
        dashboard_route = "/production/dashboard"
    elif session['role'] == "Support":
        dashboard_route = "/support/dashboard"
    else:
        dashboard_route = "/dashboard"

    return jsonify({"message": f"Logged in as {session['role']}", "redirect": dashboard_route})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "-1"
    return response

# --------------------------------------------------------------------------------------------
# SALES
# --------------------------------------------------------------------------------------------
@app.route('/sales/dashboard')
@login_required
def sales_dashboard_page():
    user_id = current_user.id
    total_requests = Request.query.filter_by(user_id=user_id).count()
    today = date.today()
    today_requests = Request.query.filter(
        Request.user_id == user_id,
        Request.timestamp >= datetime(today.year, today.month, today.day)
    ).count()
    pending_requests = Request.query.filter_by(user_id=user_id, status="Pending").count()
    fulfilled_requests = Request.query.filter_by(user_id=user_id, status="Fulfilled").count()
    declined_requests = Request.query.filter_by(user_id=user_id, status="Declined").count()
    urgent_requests = Request.query.filter_by(user_id=user_id, priority="Urgent").count()

    week_days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    week_counts = [2, 5, 3, 4, 6, 1, 0]
    category_labels = ['Product Availability', 'Pricing Query', 'Order Submit']
    category_counts = [10, 7, 3]
    trend_days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    fulfilled_trend = [1, 2, 0, 3, 1, 0, 2]
    declined_trend = [0, 1, 1, 0, 2, 1, 0]

    return render_template(
        "sales/dashboard.html",
        total_requests=total_requests,
        today_requests=today_requests,
        pending_requests=pending_requests,
        fulfilled_requests=fulfilled_requests,
        declined_requests=declined_requests,
        urgent_requests=urgent_requests,
        week_days=week_days,
        week_counts=week_counts,
        category_labels=category_labels,
        category_counts=category_counts,
        trend_days=trend_days,
        fulfilled_trend=fulfilled_trend,
        declined_trend=declined_trend
    )

@app.route('/sales/submit_request', methods=['GET', 'POST'])
@login_required
def sales_submit_request():
    if request.method == 'POST':
        data = request.get_json()
        # Validate required fields
        if not data or not data.get('product_name') or not data.get('request_type') or not data.get('qty') or not data.get('customer_id'):
            return jsonify({"error": "Missing required fields"}), 400

        # Use priority from payload, default to "Normal" if not provided
        priority = data.get('priority', 'Normal')

        # Find the customer by unique_id
        customer = Customer.query.filter_by(unique_id=data['customer_id'], created_by=current_user.id).first()
        if not customer:
            return jsonify({"error": "Invalid customer selected"}), 400

        # Create and save the request
        new_request = Request(
            user_id=current_user.id,
            customer_id=customer.id,  # <-- Save the actual customer.id
            product_name=data['product_name'],
            request_category=data['request_type'],
            qty=int(data['qty']),
            description=data.get('description', ''),
            status="Pending",
            priority=priority,
            timestamp=datetime.utcnow(),
            sla_breach=False
        )
        db.session.add(new_request)
        db.session.commit()
        return jsonify({"message": "Request submitted successfully!"})

    # GET: Only show products with stock > 0
    available_products = Inventory.query.filter(Inventory.stock > 0).all()
    customers = Customer.query.filter_by(created_by=current_user.id).all()  # Only show own customers
    return render_template(
        "sales/submit_request.html",
        available_products=available_products,
        customers=customers
    )

@app.route('/request_history')
@login_required
def request_history():
    requests = Request.query.filter_by(user_id=current_user.id).order_by(Request.timestamp.desc()).all()
    return render_template("sales/request_history.html", requests=requests)

@app.route('/sales/report')
@login_required
def sales_report():
    user_id = current_user.id

    # --- Sheet 1: All Requests ---
    requests = Request.query.filter_by(user_id=user_id).order_by(Request.timestamp.desc()).all()
    data = [{
        "ID": req.id,
        "Product Name": req.product_name,
        "Category": req.request_category,
        "Quantity": req.qty,
        "Description": req.description,
        "Status": req.status,
        "Priority": req.priority,
        "Timestamp": req.timestamp.strftime("%Y-%m-%d %H:%M"),
        "SLA Breach": req.sla_breach
    } for req in requests]
    df_requests = pd.DataFrame(data)

    # --- Sheet 2: Dashboard Data ---
    dashboard_data = {
        "Metric": [
            "Total Requests", "Today Requests", "Pending", "Fulfilled", "Declined", "Urgent"
        ],
        "Value": [
            Request.query.filter_by(user_id=user_id).count(),
            Request.query.filter(
                Request.user_id == user_id,
                Request.timestamp >= datetime(datetime.now().year, datetime.now().month, datetime.now().day)
            ).count(),
            Request.query.filter_by(user_id=user_id, status="Pending").count(),
            Request.query.filter_by(user_id=user_id, status="Fulfilled").count(),
            Request.query.filter_by(user_id=user_id, status="Declined").count(),
            Request.query.filter_by(user_id=user_id, priority="Urgent").count()
        ]
    }
    df_dashboard = pd.DataFrame(dashboard_data)

    # --- Write to Excel in memory with XlsxWriter for chart support ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_requests.to_excel(writer, index=False, sheet_name='All Requests')
        df_dashboard.to_excel(writer, index=False, sheet_name='Dashboard Charts')

        workbook  = writer.book
        worksheet = writer.sheets['Dashboard Charts']

        # Create a pie chart for the dashboard data
        chart = workbook.add_chart({'type': 'pie'})
        # The data range (row 2 to 7 for values, col 2 for values, col 1 for labels)
        chart.add_series({
            'name':       'Request Metrics',
            'categories': ['Dashboard Charts', 1, 0, 6, 0],  # B2:B7
            'values':     ['Dashboard Charts', 1, 1, 6, 1],  # C2:C7
            'data_labels': {'percentage': True, 'category': True}
        })
        chart.set_title({'name': 'Request Metrics Overview'})
        # Insert the chart into the worksheet (row, col)
        worksheet.insert_chart('D2', chart, {'x_scale': 1.5, 'y_scale': 1.5})

    output.seek(0)

    return send_file(
        output,
        download_name="sales_report.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/delete_request/<int:request_id>', methods=['DELETE'])
@login_required
def delete_request(request_id):
    req = Request.query.filter_by(id=request_id, user_id=current_user.id).first()
    if not req:
        return jsonify({"success": False, "error": "Request not found or not allowed."}), 404
    db.session.delete(req)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/sales/support/<int:request_id>', methods=['GET', 'POST'])
@login_required
def sales_support(request_id):
    request_obj = Request.query.get_or_404(request_id)
    if request.method == 'POST':
        # Create SupportTicket here...
        pass
    return render_template('sales/support.html', request_obj=request_obj)

# Example route
@app.route('/sales/support', methods=['GET', 'POST'])
@login_required
def sales_support_general():
    user_requests = Request.query.filter_by(user_id=current_user.id).all()
    # Get only this user's support tickets
    support_tickets = SupportTicket.query.filter_by(user_id=current_user.id).order_by(SupportTicket.created_at.desc()).all()
    if request.method == 'POST':
        request_id = request.form.get('request_id')
        issue_type = request.form.get('issue_type')
        description = request.form.get('description')
        ticket = SupportTicket(
            user_id=current_user.id,
            order_request_id=request_id,
            issue_type=issue_type,
            subject=issue_type,
            description=description,
            status="Open"
        )
        db.session.add(ticket)
        db.session.commit()
        flash("Support ticket submitted!", "success")
        # Re-query tickets to include the new one
        support_tickets = SupportTicket.query.filter_by(user_id=current_user.id).order_by(SupportTicket.created_at.desc()).all()
        return render_template('sales/support.html', user_requests=user_requests, support_tickets=support_tickets)
    return render_template('sales/support.html', user_requests=user_requests, support_tickets=support_tickets)
# --------------------------------------------------------------------------------------------
# CUSTOMER
# --------------------------------------------------------------------------------------------


@app.route('/sales/customers', methods=['GET', 'POST'])
@login_required
def sales_customers():
    if request.method == 'POST':
        data = request.get_json()
        name = data.get('name')
        phone = data.get('phone')
        email = data.get('email')
        if not name:
            return jsonify({"success": False, "error": "Name is required."}), 400
        # Generate a unique ID
        unique_id = str(uuid4())[:8]  # Short unique ID, or use your own logic
        customer = Customer(
            unique_id=unique_id,
            name=name,
            phone=phone,
            email=email,
            created_by=current_user.id
        )
        db.session.add(customer)
        db.session.commit()
        return jsonify({"success": True})
    customers = Customer.query.filter_by(created_by=current_user.id).order_by(Customer.id.desc()).all()
    return render_template("sales/customers.html", customers=customers)

# --------------------------------------------------------------------------------------------
# WAREHOUSE
# --------------------------------------------------------------------------------------------

# Example for your warehouse_dashboard_page route
@app.route('/warehouse/dashboard')
@login_required
def warehouse_dashboard_page():
    total_requests = Request.query.filter_by(request_category="Order Submit").count()
    pending_requests = Request.query.filter_by(request_category="Order Submit", status="Pending").count()
    fulfilled_requests = Request.query.filter_by(request_category="Order Submit", status="Fulfilled").count()
    declined_requests = Request.query.filter_by(request_category="Order Submit", status="Declined").count()

    inventory = Inventory.query.all()
    inventory_labels = [item.product_name for item in inventory]
    inventory_data = [item.stock for item in inventory]

    return render_template(
        "warehouse/dashboard.html",
        total_requests=total_requests,
        pending_requests=pending_requests,
        fulfilled_requests=fulfilled_requests,
        declined_requests=declined_requests,
        inventory_labels=inventory_labels,
        inventory_data=inventory_data
    )

@app.route('/api/warehouse/inventory', methods=['GET', 'POST'])
@login_required
def api_warehouse_inventory():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('product_name') or not data.get('stock') or not data.get('location'):
            return jsonify({"success": False, "error": "All fields are required."}), 400
        try:
            new_item = Inventory(
                product_name=data['product_name'],
                stock=int(data['stock']),
                location=data['location']
            )
            db.session.add(new_item)
            db.session.commit()
            return jsonify({"success": True})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

    # GET method: return all inventory
    inventory = Inventory.query.all()
    data = [
        {
            "id": item.id,
            "product_name": item.product_name,
            "stock": item.stock,
            "location": item.location
        }
        for item in inventory
    ]
    return jsonify(data)

@app.route('/api/warehouse/inventory/<int:item_id>', methods=['PUT'])
@login_required
def update_inventory_item(item_id):
    data = request.get_json()
    item = Inventory.query.get(item_id)
    if not item:
        return jsonify({"success": False, "error": "Item not found."}), 404
    try:
        item.product_name = data.get('product_name', item.product_name)
        item.stock = int(data.get('stock', item.stock))
        item.location = data.get('location', item.location)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/warehouse/orders')
@login_required
def warehouse_order():
    requests = Request.query.filter(
        Request.request_category == "Order Submit",
        Request.status.in_(["Pending", "In Progress"])
    ).order_by(Request.timestamp.desc()).all()
    # Build a map: product_name -> inventory object
    inventory = Inventory.query.all()
    inventory_map = {item.product_name: item for item in inventory}
    return render_template("warehouse/orders.html", requests=requests, inventory_map=inventory_map)

@app.route('/warehouse/orders/fulfill/<int:request_id>', methods=['POST'])
@login_required
def warehouse_fulfill_order(request_id):
    req = Request.query.get_or_404(request_id)
    if req.status != "Pending":
        flash("Request is not pending.", "warning")
        return redirect(url_for('warehouse_order'))
    inv = Inventory.query.filter_by(product_name=req.product_name).first()
    if not inv or inv.stock < req.qty:
        flash("Not enough stock to fulfill this request.", "danger")
        return redirect(url_for('warehouse_order'))
    inv.stock -= req.qty
    req.status = "Fulfilled"
    db.session.commit()
    flash("Order fulfilled and inventory updated.", "success")
    return redirect(url_for('warehouse_order'))


@app.route('/warehouse/orders/refill', methods=['POST'])
@login_required
def warehouse_order_refill_post():
    data = request.get_json()
    product_name = data.get('product_name')
    qty = int(data.get('qty', 0))
    if not product_name or qty <= 0:
        return jsonify({"success": False, "error": "Invalid request."}), 400

    # Find the pending warehouse request for this product and missing qty
    req = Request.query.filter_by(product_name=product_name, status="Pending").order_by(Request.timestamp.desc()).first()
    if req:
        req.status = "In Process"
        db.session.commit()

    prod_req = ProductionRequest(
        product=product_name,
        qty=qty,
        description=f"Refill request from warehouse order for {qty} units of {product_name}",
        status="Pending",
        timestamp=datetime.utcnow(),
        requested_by=current_user.id
    )
    db.session.add(prod_req)
    db.session.commit()
    return jsonify({"success": True, "message": f"Refill order sent to production for {qty} units."})

@app.route('/warehouse/orders/decline/<int:request_id>', methods=['POST'])
@login_required
def warehouse_decline_order(request_id):
    req = Request.query.get_or_404(request_id)
    if req.status != "Pending":
        flash("Request is not pending.", "warning")
        return redirect(url_for('warehouse_order'))
    req.status = "Declined"
    db.session.commit()
    flash("Order declined.", "info")
    return redirect(url_for('warehouse_order'))

@app.route('/warehouse/inventory/refill', methods=['POST'])
@login_required
def warehouse_inventory_refill():
    data = request.get_json()
    item_id = data.get('item_id')
    qty = int(data.get('qty', 0))
    if not item_id or qty <= 0:
        return jsonify({"success": False, "error": "Invalid request."}), 400

    item = Inventory.query.get(item_id)
    if not item:
        return jsonify({"success": False, "error": "Inventory item not found."}), 404

    prod_req = ProductionRequest(
        product=item.product_name,
        qty=qty,
        description=f"Refill request from warehouse inventory (item #{item.id})",
        status="Pending",
        timestamp=datetime.utcnow(),
        requested_by=current_user.id
    )
    db.session.add(prod_req)
    db.session.commit()
    return jsonify({"success": True, "message": f"Refill order sent to production for {qty} units."})

@app.route('/warehouse/inventory')
@login_required
def warehouse_inventory():
    return render_template("warehouse/inventory.html")


@app.route('/warehouse/report')
@login_required
def warehouse_report():
    # --- Sheet 1: All Warehouse Requests ---
    warehouse_requests = Request.query.filter_by(request_category="Order Submit").order_by(Request.timestamp.desc()).all()
    data_requests = [{
        "ID": req.id,
        "Product Name": req.product_name,
        "Quantity": req.qty,
        "Status": req.status,
        "Priority": req.priority,
        "Timestamp": req.timestamp.strftime("%Y-%m-%d %H:%M"),
        "Description": req.description
    } for req in warehouse_requests]
    df_requests = pd.DataFrame(data_requests)

    # --- Sheet 2: Inventory ---
    inventory = Inventory.query.all()
    data_inventory = [{
        "ID": item.id,
        "Product Name": item.product_name,
        "Stock": item.stock,
        "Location": item.location
    } for item in inventory]
    df_inventory = pd.DataFrame(data_inventory)

    # --- Sheet 3: Dashboard Data ---
    dashboard_data = {
        "Metric": [
            "Total Requests", "Pending", "Fulfilled", "Declined"
        ],
        "Value": [
            Request.query.filter_by(request_category="Order Submit").count(),
            Request.query.filter_by(request_category="Order Submit", status="Pending").count(),
            Request.query.filter_by(request_category="Order Submit", status="Fulfilled").count(),
            Request.query.filter_by(request_category="Order Submit", status="Declined").count()
        ]
    }
    df_dashboard = pd.DataFrame(dashboard_data)

    # --- Write to Excel in memory with XlsxWriter for chart support ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_requests.to_excel(writer, index=False, sheet_name='Warehouse Requests')
        df_inventory.to_excel(writer, index=False, sheet_name='Inventory')
        df_dashboard.to_excel(writer, index=False, sheet_name='Dashboard Charts')

        workbook  = writer.book
        worksheet = writer.sheets['Dashboard Charts']

        # Create a pie chart for the dashboard data
        chart = workbook.add_chart({'type': 'pie'})
        chart.add_series({
            'name':       'Warehouse Metrics',
            'categories': ['Dashboard Charts', 1, 0, 4, 0],  # Metrics
            'values':     ['Dashboard Charts', 1, 1, 4, 1],  # Values
            'data_labels': {'percentage': True, 'category': True}
        })
        chart.set_title({'name': 'Warehouse Metrics Overview'})
        worksheet.insert_chart('D2', chart, {'x_scale': 1.5, 'y_scale': 1.5})

    output.seek(0)

    return send_file(
        output,
        download_name="warehouse_report.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# --------------------------------------------------------------------------------------------
# PRODUCTION
# --------------------------------------------------------------------------------------------


@app.route('/production/resources')
@login_required
def production_resources():
    resources = Resource.query.all()
    return render_template("production/resources.html", resources=resources)

@app.route('/production/dashboard')
@login_required
def production_dashboard():
    total_requests = ProductionRequest.query.count()
    pending = ProductionRequest.query.filter_by(status="Pending").count()
    in_progress = ProductionRequest.query.filter_by(status="In Progress").count()
    completed = ProductionRequest.query.filter_by(status="Completed").count()
    declined = ProductionRequest.query.filter_by(status="Declined").count()
    return render_template(
        "production/dashboard.html",
        total_requests=total_requests,
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        declined=declined
    )

@app.route('/production/orders')
@login_required
def production_orders():
    # Only show orders that are NOT "In Process"
    requests = ProductionRequest.query.filter(ProductionRequest.status != "In Process").order_by(ProductionRequest.timestamp.desc()).all()
    products = Product.query.all()
    resources_by_product = {}
    for product in products:
        normalized_name = product.name.strip().lower()
        resources_by_product[normalized_name] = [
            {
                "name": pr.resource.name,
                "type": pr.resource.type,
                "quantity": pr.per_unit
            }
            for pr in product.product_resources 
        ]

    print("resources_by_product keys:", list(resources_by_product.keys()))
    for req in requests:
        print("Request product:", req.product, "->", req.product.strip().lower())

    return render_template(
        "production/orders.html",
        requests=requests,
        resources_by_product=resources_by_product
    )

@app.route('/production/orders/produce/<int:request_id>', methods=['POST'])
@login_required
def production_start_process(request_id):
    if not request.is_json:
        return jsonify({"success": False, "error": "Request must be JSON"}), 415
    data = request.get_json()

    ref = ProductionRequest.query.get_or_404(request_id)
    product = Product.query.filter_by(name=ref.product).first()
    if not product:
        return jsonify({"success": False, "error": "Product not found."})

    unavailable = []
    # Check resource stock for each required resource
    for pr in product.product_resources:
        resource = pr.resource
        required_qty = pr.per_unit * ref.qty
        print("Checking resource stock for:", resource.name.strip().lower())
        if resource.quantity < required_qty:
            unavailable.append(f"{resource.name} (needed: {required_qty}, available: {resource.quantity})")

    if unavailable:
        return jsonify({"success": False, "unavailable": unavailable})

    # Deduct resources from resource stock
    for pr in product.product_resources:
        resource = pr.resource
        required_qty = pr.per_unit * ref.qty
        resource.quantity -= required_qty

    ref.status = "In Process"
    db.session.commit()
    return jsonify({"success": True})

@app.route('/production/orders/complete/<int:request_id>', methods=['POST'])
@login_required
def production_complete_order(request_id):
    ref = ProductionRequest.query.get_or_404(request_id)
    if ref.status != "In Process":
        flash("Order is not in process.", "warning")
        return redirect(url_for('production_in_process'))

    # Update inventory with produced items
    inv = Inventory.query.filter_by(product_name=ref.product).first()
    if inv:
        inv.stock += ref.qty
    else:
        inv = Inventory(product_name=ref.product, stock=ref.qty)
        db.session.add(inv)

    # Check for warehouse orders "In Process" for this product
    pending_requests = Request.query.filter_by(product_name=ref.product, status="In Process").all()
    for req in pending_requests:
        inventory = Inventory.query.filter_by(product_name=req.product_name).first()
        if inventory and inventory.stock >= req.qty:
            req.status = "Pending"  # Now can be fulfilled
    ref.status = "Completed"
    db.session.commit()
    flash("Production completed and inventory updated.", "success")
    return redirect(url_for('production_in_process'))

@app.route('/production/in_process')
@login_required
def production_in_process():
    in_process_orders = ProductionRequest.query.filter_by(status="In Process").order_by(ProductionRequest.timestamp.desc()).all()
    return render_template("production/in_process.html", orders=in_process_orders)

@app.route('/api/production/resource/<int:resource_id>', methods=['PUT'])
def update_resource_quantity(resource_id):
    data = request.get_json()
    qty = data.get('quantity')
    if qty is None or int(qty) < 0:
        return jsonify({"success": False, "error": "Invalid quantity."}), 400
    resource = Resource.query.get(resource_id)
    if not resource:
        return jsonify({"success": False, "error": "Resource not found."}), 404
    resource.quantity = int(qty)
    db.session.commit()
    return jsonify({"success": True})

@app.route('/production/report')
@login_required
def production_report():
    # --- Sheet 1: All Production Orders ---
    production_orders = ProductionRequest.query.order_by(ProductionRequest.timestamp.desc()).all()
    data_orders = [{
        "ID": order.id,
        "Product Name": order.product,
        "Quantity": order.qty,
        "Status": order.status,
        "Priority": getattr(order, 'priority', ''),
        "Timestamp": order.timestamp.strftime("%Y-%m-%d %H:%M"),
        "Description": order.description
    } for order in production_orders]
    df_orders = pd.DataFrame(data_orders)

    # --- Sheet 2: All Resources ---
    resources = Resource.query.all()
    data_resources = [{
        "ID": res.id,
        "Name": res.name,
        "Type": res.type,
        "Quantity": res.quantity
    } for res in resources]
    df_resources = pd.DataFrame(data_resources)

    # --- Sheet 3: Dashboard Data ---
    dashboard_data = {
        "Metric": [
            "Total Orders", "Pending", "In Process", "Completed", "Declined"
        ],
        "Value": [
            ProductionRequest.query.count(),
            ProductionRequest.query.filter_by(status="Pending").count(),
            ProductionRequest.query.filter_by(status="In Process").count(),
            ProductionRequest.query.filter_by(status="Completed").count(),
            ProductionRequest.query.filter_by(status="Declined").count()
        ]
    }
    df_dashboard = pd.DataFrame(dashboard_data)

    # --- Write to Excel in memory with XlsxWriter for chart support ---
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_orders.to_excel(writer, index=False, sheet_name='All Production Orders')
        df_resources.to_excel(writer, index=False, sheet_name='Resources')
        df_dashboard.to_excel(writer, index=False, sheet_name='Dashboard Charts')

        workbook  = writer.book
        worksheet = writer.sheets['Dashboard Charts']

        # Create a pie chart for the dashboard data
        chart = workbook.add_chart({'type': 'pie'})
        chart.add_series({
            'name':       'Production Metrics',
            'categories': ['Dashboard Charts', 1, 0, 5, 0],  # Metrics
            'values':     ['Dashboard Charts', 1, 1, 5, 1],  # Values
            'data_labels': {'percentage': True, 'category': True}
        })
        chart.set_title({'name': 'Production Metrics Overview'})
        worksheet.insert_chart('D2', chart, {'x_scale': 1.5, 'y_scale': 1.5})

    output.seek(0)

    return send_file(
        output,
        download_name="production_report.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# --------------------------------------------------------------------------------------------
# SUPPORT
# --------------------------------------------------------------------------------------------


@app.route('/support/dashboard')
@login_required
def support_dashboard():
    # Example queries (adjust as needed for your model)
    open_tickets = SupportTicket.query.filter_by(status="Open").count()
    resolved_tickets = SupportTicket.query.filter_by(status="Resolved").count()
    # Example: average response time (dummy value here)
    avg_response_time = "2h 15m"  # Replace with real calculation if needed

    # Recent tickets (last 10)
    recent_tickets = SupportTicket.query.order_by(SupportTicket.updated_at.desc()).limit(10).all()

    return render_template(
        "support/dashboard.html",
        open_tickets=open_tickets,
        resolved_tickets=resolved_tickets,
        avg_response_time=avg_response_time,
        recent_tickets=recent_tickets
    )

@app.route('/support/submit_ticket', methods=['GET', 'POST'])
@login_required
def submit_ticket():
    order_request_id = request.args.get('order_request_id')
    user_requests = Request.query.all()  # Or filter as needed
    if request.method == 'POST':
        order_request_id = request.form.get('order_request_id')
        issue_type = request.form.get('issue_type')
        description = request.form.get('description')
        # Use issue_type as subject
        ticket = SupportTicket(
            user_id=current_user.id,
            order_request_id=order_request_id,
            issue_type=issue_type,
            subject=issue_type,  # <-- Set subject to issue_type
            description=description,
            status="Open",
            priority="Normal"
        )
        db.session.add(ticket)
        db.session.commit()
        flash("Support ticket submitted!", "success")
        return render_template('support/submit_ticket.html', user_requests=user_requests, order_request_id=order_request_id)
    return render_template('support/submit_ticket.html', user_requests=user_requests, order_request_id=order_request_id)

@app.route('/support/tickets')
@login_required
def support_tickets():
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    return render_template("support/tickets.html", tickets=tickets)

@app.route('/support/reports')
@login_required
def support_reports():
    # Example: show support performance reports
    return render_template('support/reports.html')

@app.route('/support/all_requests')
@login_required
def support_all_requests():
    # Only allow support agents (optional: add a role check)
    all_requests = Request.query.order_by(Request.timestamp.desc()).all()
    return render_template('support/all_requests.html', all_requests=all_requests)

@app.route('/support/ticket/<int:ticket_id>/reply', methods=['POST'])
@login_required
def support_ticket_reply(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    reply_text = request.form['reply']
    module = request.form['module']
    # Save the reply
    reply = TicketReply(
        ticket_id=ticket.id,
        sender_name=current_user.email,
        message=reply_text,
        module=module
    )
    db.session.add(reply)
    # Change status to Closed
    ticket.status = "Closed"
    db.session.commit()
    flash('Reply sent and ticket closed.', 'success')
    return redirect(request.referrer or url_for('support_tickets'))