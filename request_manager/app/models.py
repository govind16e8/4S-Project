from app import db
import datetime
from datetime import datetime
from flask_login import UserMixin

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # Add password column

class WarehouseRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    request_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="Pending")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    sla_breach = db.Column(db.Boolean, default=False)

class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_request_id = db.Column(db.Integer, db.ForeignKey('request.id'), nullable=True)  # Link to sales order
    order_request = db.relationship('Request')
    issue_type = db.Column(db.String(100), nullable=False)  
    subject = db.Column(db.String(200), nullable=True)  # <-- Add this line
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="Pending")
    priority = db.Column(db.String(20), default="Normal")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # Add this line
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)  # Add this line

    # If you had 'timestamp' before, you can remove it or keep it for backward compatibility
class ProductionRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(100), nullable=False)  # Product name
    qty = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    status = db.Column(db.String(50), default="Pending")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(100), nullable=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unique_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    creator = db.relationship('User', backref='customers')

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # e.g., 'Material', 'Machine', 'Staff'
    quantity = db.Column(db.Integer, nullable=False, default=0)

# Example model addition
class OrderStatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('request.id'))
    status = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.String(255))

class ResourceInventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(100), nullable=True)
    resource = db.relationship('Resource')

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    # Optionally, add more fields (category, description, etc.)

class ProductResource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    per_unit = db.Column(db.Integer, nullable=False)  # How much of this resource per product unit

    product = db.relationship('Product', backref='product_resources')
    resource = db.relationship('Resource', backref='product_resources')

class RequestHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('request.id'), nullable=False)
    previous_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class Request(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    customer = db.relationship('Customer')
    product_name = db.Column(db.String(100), nullable=False)
    request_category = db.Column(db.String(100), nullable=False)
    qty = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="Pending")
    priority = db.Column(db.String(20), default="Normal")  # "Urgent" or "Normal"
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    sla_breach = db.Column(db.Boolean, default=False)

    def update_status(self, new_status):
        """Ensure status change follows valid transitions"""
        valid_transitions = {
            "Submitted": ["In Review", "Canceled"],
            "In Review": ["Fulfilled", "Delayed", "Canceled"],
            "Delayed": ["In Review", "Canceled"],
            "Fulfilled": [],  # Final status
            "Declined": []    # Final status if SLA breached
        }
        if new_status in valid_transitions.get(self.status, []):
            self.status = new_status
            db.session.commit()
            return True
        return False  # Invalid transition

    def check_sla_breach(self):
        """Check if SLA is breached and update status if needed."""
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        sla_days = 2 if self.priority == "Urgent" else 3
        if self.status not in ["Fulfilled", "Declined"]:
            if (now - self.timestamp).days > sla_days:
                self.sla_breach = True
                self.status = "Declined"
                db.session.commit()
                return True
        return False
    
class TicketReply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False)
    sender_name = db.Column(db.String(128), nullable=False)
    message = db.Column(db.Text, nullable=False)
    module = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ticket = db.relationship('SupportTicket', backref=db.backref('replies', lazy=True))

