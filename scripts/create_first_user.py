import logging
import sys
import os

# Fix path to import 'app'
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.session import SessionLocal
from app.db.models import User
from app.core.security import get_password_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CreateUser")

def create_admin_user():
    db = SessionLocal()
    
    email = "admin@unicef.org"
    password = "admin_password_123" # Cambiar en producción
    
    # Check if exists
    user = db.query(User).filter(User.email == email).first()
    if user:
        logger.info(f"User {email} already exists.")
        return

    logger.info(f"Creating admin user: {email}")
    
    new_user = User(
        email=email,
        hashed_password=get_password_hash(password),
        full_name="UNICEF Admin",
        role="admin",
        is_active=True
    )
    
    db.add(new_user)
    db.commit()
    logger.info("✅ Admin user created successfully.")
    db.close()

if __name__ == "__main__":
    create_admin_user()