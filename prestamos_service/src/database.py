import os
from sqlite3.dbapi2 import DatabaseError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./prestamos.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """Clase base para los modelos ORM de la base de datos del sistema de 'Préstamos'."""
    pass

def get_db():
    """Dependencia para los endpoints HTTP de FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
