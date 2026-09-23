import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models import Organization, User
from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CreateUser")

MIN_PASSWORD_LENGTH = 12
# Values shipped in .env.example / docker-compose and other obvious defaults.
PLACEHOLDER_PASSWORDS = {"change_me_now", "change_me", "password", "admin", "admin123"}

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
    if len(password) < MIN_PASSWORD_LENGTH or password.lower() in PLACEHOLDER_PASSWORDS:
        raise RuntimeError(
            f"FIRST_SUPERUSER_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters "
            "and must not be an example value."
        )
    if len(password.encode("utf-8")) > 72:
        # bcrypt silently ignores everything past 72 bytes.
        raise RuntimeError("FIRST_SUPERUSER_PASSWORD must be at most 72 bytes.")
    
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

        # If the superadmin user already exists, do nothing. Re-running the
        # seed must never reset an existing account's password or re-elevate
        # its role — that would silently undo any later administrative change.
        user = db.query(User).filter(User.email == email).first()
        if user:
            logger.info(f"User {email} already exists — leaving it untouched.")
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
        # Only the type: driver messages can embed the email or password hash.
        logger.error("Failed to setup superadmin: %s", type(e).__name__)
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    try:
        create_superadmin()
    except Exception:
        sys.exit(1)