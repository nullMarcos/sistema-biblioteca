"""
Script para inicializar la base de datos de prestamos_service con datos de prueba.
Crea 3 socios de prueba, de los cuales solo uno tiene un préstamo activo asignado.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

# Permitir ejecución tanto directa como modular
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import Base, engine, SessionLocal
from src.models import Socio, Prestamo

# 3 socios de prueba: solo uno con préstamo activo asignado
SOCIOS_SEMILLA: List[Dict[str, Any]] = [
    {
        "nombre": "Ana García Morales",
        "correo": "ana.garcia@example.com",
        "prestamos": [
            {
                # ID lógico correspondiente a un ejemplar en estado PRESTADO en Catálogo
                # (por ejemplo, Dune con libro_id=1, ejemplar_id=3)
                "libro_id": 1,
                "ejemplar_id": 3,
                "fecha_prestamo": datetime.now(timezone.utc) - timedelta(days=2),
                "fecha_limite": date.today() + timedelta(days=12),
                "fecha_devolucion": None,
                "estado": "ACTIVO",
            }
        ],
    },
    {
        "nombre": "Carlos Mendoza Silva",
        "correo": "carlos.mendoza@example.com",
        "prestamos": [],
    },
    {
        "nombre": "Lucía Valenzuela Pinto",
        "correo": "lucia.valenzuela@example.com",
        "prestamos": [],
    },
]


def seed_database(session: Optional[Session] = None, reset: bool = False) -> Dict[str, int]:
    """
    Puebla la base de datos con los socios y préstamos iniciales.

    :param session: Sesión de SQLAlchemy opcional. Si no se provee, se crea una nueva.
    :param reset: Si es True, elimina los registros existentes antes de insertar.
    :return: Diccionario con el conteo de socios y préstamos creados.
    """
    should_close = False
    if session is None:
        session = SessionLocal()
        should_close = True

    try:
        # Asegurar que las tablas existan
        Base.metadata.create_all(bind=engine)

        if reset:
            session.query(Prestamo).delete()
            session.query(Socio).delete()
            session.commit()
            print("ℹ️  Tablas 'prestamos' y 'socios' limpiadas exitosamente.")
        else:
            existing_count = session.query(Socio).count()
            if existing_count > 0:
                print(f"⚠️  La base de datos ya contiene {existing_count} socios. Usa reset=True o el flag --reset para recargar.")
                return {"socios": 0, "prestamos": 0}

        total_socios = 0
        total_prestamos = 0

        for data in SOCIOS_SEMILLA:
            prestamos_objs = [
                Prestamo(
                    libro_id=p["libro_id"],
                    ejemplar_id=p["ejemplar_id"],
                    fecha_prestamo=p["fecha_prestamo"],
                    fecha_limite=p["fecha_limite"],
                    fecha_devolucion=p["fecha_devolucion"],
                    estado=p["estado"],
                )
                for p in data["prestamos"]
            ]

            socio = Socio(
                nombre=data["nombre"],
                correo=data["correo"],
                prestamos=prestamos_objs,
            )
            session.add(socio)
            total_socios += 1
            total_prestamos += len(prestamos_objs)

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
    parser.add_argument("--reset", action="store_true", help="Elimina los datos existentes antes de poblar.")
    args = parser.parse_args()

    seed_database(reset=args.reset)


if __name__ == "__main__":
    main()
