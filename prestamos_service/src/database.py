import os
import runpy
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from src.presupuestos import restante

# Obteiene la URL de la base de datos desde las variables de entorno, si no existe entonces por default recurre prestamos.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./prestamos.db")

# Creacion del motor de conexion, el parámetro {"check_same_thread": False} permite peticiones por multiples hilos
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": float(os.getenv("SQLITE_BUSY_TIMEOUT", "0.5"))},
)

# Clase Factory, se encarga de crear sesiones ligadas al engine
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """Clase base de la cual heredarán todos los modelos ORM de la base de datos del sistema de 'Préstamos'."""
    pass


@contextmanager
def escritura(db):
    """Transacción local breve; no invocar dependencias remotas dentro de ella."""
    if db.in_transaction():
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("La sesión contiene cambios fuera de la transacción del caso de uso")
        db.rollback()
    try:
        restante()
        db.execute(text("BEGIN IMMEDIATE"))
        yield
        restante()
        db.commit()
    except Exception:
        db.rollback()
        raise


def migrar_esquema():
    servicio = Path(__file__).resolve().parents[1]
    runner = servicio / "scripts/migrar_sqlite.py"
    if not runner.is_file():
        runner = servicio.parent / "scripts/migrar_sqlite.py"
    runpy.run_path(str(runner))["aplicar"](Path(engine.url.database), servicio / "migrations")

def get_db():
    """Funcion generador, dependencia para los endpoints HTTP de FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
