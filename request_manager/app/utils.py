from datetime import datetime, timedelta
import pandas as pd
from app.models import Request
from flask_login import LoginManager
from app.models import User
from app import app, db

login_manager = LoginManager()
login_manager.init_app(app)  # Now app is properly defined

login_manager.login_view = "login"  # Redirect unauthorized users to login

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

SLA_THRESHOLD = timedelta(days=2)  # Requests must be fulfilled within 2 days

def check_sla_breach(request):
    if request.status != "Fulfilled":
        time_elapsed = datetime.utcnow() - request.timestamp
        return time_elapsed > SLA_THRESHOLD
    return False

def generate_sla_report():
    """Generates an Excel report of requests and SLA breaches"""
    requests = Request.query.all()
    
    data = [
        {"Request ID": req.id, "User ID": req.user_id, "Type": req.request_type,
         "Status": req.status, "Timestamp": req.timestamp, "SLA Breach": req.sla_breach}
        for req in requests
    ]
    
    df = pd.DataFrame(data)
    df.to_excel("sla_report.xlsx", index=False)
    return "Excel report generated: sla_report.xlsx"