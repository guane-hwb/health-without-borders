import logging
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.session import SessionLocal
from app.db.models import User, Organization
from app.core.security import get_password_hash
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CreateUser")

def create_superadmin():
    db = SessionLocal()
    
    # Leemos TODAS las credenciales dinámicamente desde el entorno
    email = settings.FIRST_SUPERUSER_EMAIL
    password = settings.FIRST_SUPERUSER_PASSWORD
    org_name = settings.ROOT_ORGANIZATION_NAME 

    if not email or not password:
        raise RuntimeError(
            "FIRST_SUPERUSER_EMAIL and FIRST_SUPERUSER_PASSWORD must be explicitly configured."
        )
    
    try:
        # Check if the root organization exists, if not create it
        master_org = db.query(Organization).filter(Organization.name == org_name).first()
        
        if not master_org:
            logger.info(f"Creating Root Master Organization: '{org_name}'...")
            master_org = Organization(name=org_name, is_active=True)
            db.add(master_org)
            db.commit()
            db.refresh(master_org)
            logger.info(f"Root Organization created with ID: {master_org.id}")

        # Check if the superadmin user already exists, if so, update password and role
        user = db.query(User).filter(User.email == email).first()
        if user:
            logger.info(f"User {email} already exists.")
            user.hashed_password = get_password_hash(password)
            user.role = "superadmin"
            user.organization_id = master_org.id
            db.commit()
            logger.info(f"Password and roles updated for existing user: {email}")
            return

        # Create the superadmin user
        logger.info(f"Creating superadmin user: {email}")
        new_user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name="Global Admin",
            role="superadmin",
            is_active=True,
            organization_id=master_org.id
        )
        
        db.add(new_user)
        db.commit()
        logger.info("SuperAdmin user created successfully.")
        
    except Exception as e:
        logger.error(f"Failed to setup superadmin: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_superadmin()