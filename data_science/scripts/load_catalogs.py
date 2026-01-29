import json
import os
import sys
from sqlalchemy.orm import Session

sys.path.append(os.getcwd())

from app.db.session import engine
from app.db.models import DiagnosisCIE10, VaccineCVX

def load_cie10():
    file_path = "data_science/raw/cie10.json"
    data = []

    # 1. Leer el archivo (o usar datos de prueba si no existe aún)
    if os.path.exists(file_path):
        print(f"Reading file {file_path}...")
        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

            for item in raw_data:
                code = item.get('code')
                desc = item.get('description')
                if code and desc:
                    data.append(DiagnosisCIE10(code=code, description=desc))
    else:
        print("⚠️ cie10.json NO found, loading test data ...")
        data = [
            DiagnosisCIE10(code="A09", description="Diarrea y gastroenteritis de presunto origen infeccioso", is_common=True),
            DiagnosisCIE10(code="J00", description="Rinofaringitis aguda [resfriado común]", is_common=True),
            DiagnosisCIE10(code="E40", description="Kwashiorkor (Desnutrición severa)", is_common=True),
        ]

    # 2. Insertar en Base de Datos
    with Session(engine) as session:
        print(f"Inserting {len(data)} diagnoses...")
        # Opción rápida: borrar todo lo anterior para no duplicar en pruebas
        session.query(DiagnosisCIE10).delete()
        
        # Insertar masivamente
        session.add_all(data)
        session.commit()
        print("✅ Diagnoses loaded.")

def load_vaccines():
    # Datos manuales de las vacunas más comunes (CVX Standard)
    # Fuente: CDC HL7 Table 0292
    vaccines = [
        VaccineCVX(code=90707, name="MMR (Sarampión, Rubeola, Paperas)"), # SRP
        VaccineCVX(code=90700, name="DTaP (Difteria, Tétanos, Tosferina)"),
        VaccineCVX(code=90713, name="IPV (Polio Inactivada)"),
        VaccineCVX(code=90716, name="Varicela"),
        VaccineCVX(code=90723, name="DTaP-HepB-IPV (Pentavalente)"),
        VaccineCVX(code=90681, name="Rotavirus"),
        VaccineCVX(code=90670, name="Neumococo Conjugada (PCV13)"),
        VaccineCVX(code=90717, name="Fiebre Amarilla"),
        VaccineCVX(code=90746, name="Hepatitis B"),
        VaccineCVX(code=90712, name="OPV (Polio Oral)"),
    ]
    
    with Session(engine) as session:
        print(f"Inserting {len(vaccines)} vaccines...")
        session.query(VaccineCVX).delete()
        session.add_all(vaccines)
        session.commit()
        print("✅ Vaccines loaded.")

if __name__ == "__main__":
    load_cie10()
    load_vaccines()