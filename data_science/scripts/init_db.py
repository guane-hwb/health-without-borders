# init_db.py
import asyncio
from app.db.session import engine
from app.db.base import Base
from app.db.models import Patient, DiagnosisCIE10, VaccineCVX

def init_db():
    print("Creando tablas en la base de datos...")
    # Esto crea todas las tablas definidas en los modelos importados arriba
    Base.metadata.create_all(bind=engine)
    print("✅ Tablas creadas exitosamente: patients, catalog_cie10, catalog_vaccines")

if __name__ == "__main__":
    init_db()