import sys
sys.path.append("..")  # Ensure parent directory is accessible

from app import app, db
from app.models import User
from werkzeug.security import generate_password_hash

# Predefined users
default_users = [
    {"email": "sales@sales.com", "password": "sales123"},
    {"email": "sales1@sales.com", "password": "sales123"},
    {"email": "sales2@sales.com", "password": "sales123"},
    {"email": "sales3@sales.com", "password": "sales123"},
    {"email": "sales4@sales.com", "password": "sales123"},
    {"email": "warehouse@warehouse.com", "password": "warehouse123"},
    {"email": "production@production.com", "password": "production123"},
    {"email": "support@support.com", "password": "support123"},
]

with app.app_context():
    for user_data in default_users:
        existing_user = User.query.filter_by(email=user_data["email"]).first()
        if not existing_user:
            new_user = User(
                email=user_data["email"],
                password=generate_password_hash(user_data["password"]),
                # role="Sales"  # Uncomment if your User model has a role field
            )
            db.session.add(new_user)

    db.session.commit()
    print("Default users added successfully!")