"""
Script para inicializar la base de datos de prestamos_service con datos de prueba.
Crea 3 socios de prueba; los préstamos se solicitan mediante la Saga real.
"""

import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

# Permitir ejecución tanto directa como modular
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import Base, SessionLocal, migrar_esquema
from src.models import Socio, Prestamo, SagaTransaccion, DevolucionOperacion

# 3 socios de prueba sin asumir IDs de ejemplares de otra base.
SOCIOS_SEMILLA: List[Dict[str, Any]] = [
    {
        "rut": "11.111.111-1",
        "nombre": "Ana García Morales",
        "correo": "ana.garcia@example.com",
    },
    {
        "rut": "22.222.222-2",
        "nombre": "Carlos Mendoza Silva",
        "correo": "carlos.mendoza@example.com",
    },
    {
        "rut": "33.333.333-3",
        "nombre": "Lucía Valenzuela Pinto",
        "correo": "lucia.valenzuela@example.com",
    },
]


def seed_database(session: Optional[Session] = None, reset: bool = False) -> Dict[str, int]:
    """
    Puebla la base de datos con los socios y préstamos iniciales.

    :param session: Sesión de SQLAlchemy opcional. Si no se provee, se crea una nueva.
    :param reset: Compatibilidad CLI; solo se acepta sobre una base vacía.
    :return: Diccionario con el conteo de socios y préstamos creados.
    """
    should_close = False
    if session is None:
        session = SessionLocal()
        should_close = True

    try:
        Base.metadata.create_all(bind=session.get_bind())
        if session.new or session.dirty or session.deleted:
            raise ValueError("La semilla requiere una sesión sin cambios pendientes")
        session.execute(text("BEGIN IMMEDIATE"))
        if any(session.query(modelo).first() is not None for modelo in (Socio, Prestamo, SagaTransaccion, DevolucionOperacion)):
            if reset:
                raise ValueError("No se reinicializa una base poblada; use una base desechable nueva")
            session.rollback()
            return {"socios": 0, "prestamos": 0}

        total_socios = 0
        total_prestamos = 0

        for data in SOCIOS_SEMILLA:
            socio = Socio(
                rut=data["rut"],
                nombre=data["nombre"],
                correo=data["correo"],
            )
            session.add(socio)
            total_socios += 1

        session.commit()
        print(f"✅ Población completada con éxito: {total_socios} socios y {total_prestamos} préstamo(s) insertados.")
        return {"socios": total_socios, "prestamos": total_prestamos}

    except Exception as e:
        session.rollback()
        print(f"❌ Error durante el seed: {e}")
        raise
    finally:
        if should_close:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Poblar la base de datos de prestamos_service con datos iniciales.")
    parser.add_argument("--reset", action="store_true", help="Solo se acepta si la base está vacía.")
    args = parser.parse_args()

    migrar_esquema()
    seed_database(reset=args.reset)


if __name__ == "__main__":
    main()
