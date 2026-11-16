import os
import runpy
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./catalogo.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": float(os.getenv("SQLITE_BUSY_TIMEOUT", "0.5"))},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """Clase base para los modelos ORM de la base de datos del sistema de 'Catálogo'."""
    pass


def iniciar_escritura(session):
    """Adquirir el escritor SQLite antes de leer; funciona entre procesos."""
    session.execute(text("BEGIN IMMEDIATE"))


def migrar_esquema():
    """Usar el mismo runner versionado en Docker y en ejecucion local."""
    servicio = Path(__file__).resolve().parents[1]
    runner = servicio / "scripts/migrar_sqlite.py"
    if not runner.is_file():
        runner = servicio.parent / "scripts/migrar_sqlite.py"
    runpy.run_path(str(runner))["aplicar"](Path(engine.url.database), servicio / "migrations")
