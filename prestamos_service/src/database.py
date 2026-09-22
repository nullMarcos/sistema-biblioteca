import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Obteiene la URL de la base de datos desde las variables de entorno, si no existe entonces por default recurre prestamos.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./prestamos.db")

# Creacion del motor de conexion, el parámetro {"check_same_thread": False} permite peticiones por multiples hilos
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Clase Factory, se encarga de crear sesiones ligadas al engine
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """Clase base de la cual heredarán todos los modelos ORM de la base de datos del sistema de 'Préstamos'."""
    pass

def get_db():
    """Funcion generador, dependencia para los endpoints HTTP de FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
