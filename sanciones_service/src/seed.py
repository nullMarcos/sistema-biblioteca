"""
Script para poblar la base de datos de sanciones_service con datos iniciales de prueba.
Alineado con los socios de prueba de prestamos_service.
"""

import sys
import argparse
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from sqlalchemy.orm import Session

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import Base, engine, SessionLocal
from src.models import Socio, Bloqueo, Multa


def seed_database(session: Optional[Session] = None, reset: bool = False) -> Dict[str, int]:
    """
    Puebla la base de datos con socios de prueba, incluyendo bloqueos y multas.
    """
    should_close = False
    if session is None:
        session = SessionLocal()
        should_close = True

    try:
        Base.metadata.create_all(bind=engine)

        if reset:
            session.query(Multa).delete()
            session.query(Bloqueo).delete()
            session.query(Socio).delete()
            session.commit()
            print("ℹ️  Tablas de sanciones limpiadas exitosamente.")
        else:
            existing = session.query(Socio).count()
            if existing > 0:
                return {"socios": 0, "bloqueos": 0, "multas": 0}

        ahora = datetime.now(timezone.utc)

        # 1. Socio limpio (Camino feliz)
        socio_limpio = Socio(
            rut="11.111.111-1",
            nombre="Ana García Morales",
            correo="ana.garcia@example.com",
        )

        # 2. Socio sancionado (Para probar rechazos / revisión en Saga)
        socio_sancionado = Socio(
            rut="22.222.222-2",
            nombre="Carlos Mendoza Silva",
            correo="carlos.mendoza@example.com",
            bloqueos=[
                Bloqueo(
                    motivo="Suspensión temporal por atraso reiterado",
                    fecha_inicio=ahora - timedelta(days=5),
                    fecha_fin=ahora + timedelta(days=10),
                    estado="ACTIVO",
                )
            ],
            multas=[
                Multa(
                    tipo="ATRASO",
                    libro_id=1,
                    ejemplar_id=2,
                    dias_atraso=5,
                    monto=Decimal("2500.00"),
                    estado="PENDIENTE",
                    fecha_emision=ahora - timedelta(days=5),
                )
            ],
        )

        # 3. Socio limpio alternativo
        socio_libre = Socio(
            rut="33.333.333-3",
            nombre="Lucía Valenzuela Pinto",
            correo="lucia.valenzuela@example.com",
        )

        session.add_all([socio_limpio, socio_sancionado, socio_libre])
        session.commit()

        print("✅ Base de datos de Sanciones poblada exitosamente con datos de prueba.")
        return {"socios": 3, "bloqueos": 1, "multas": 1}

    except Exception as e:
        session.rollback()
        print(f"❌ Error durante el seed de Sanciones: {e}")
        raise
    finally:
        if should_close:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Poblar base de datos de sanciones_service.")
    parser.add_argument("--reset", action="store_true", help="Elimina los datos existentes antes de poblar.")
    args = parser.parse_args()
    seed_database(reset=args.reset)


if __name__ == "__main__":
    main()
