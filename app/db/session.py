from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv() # Carga las variables del .env

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Crear el motor de conexión
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Crear la fábrica de sesiones
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Función de dependencia para obtener la DB en cada request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()