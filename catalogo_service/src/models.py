from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Index
from typing import List
from src.database import Base

class Libro(Base):
    __tablename__ = "libros"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    titulo: Mapped[str] = mapped_column(String(100), nullable=False)
    autor: Mapped[str] = mapped_column(String(100), nullable=False)
    genero: Mapped[str] = mapped_column(String(100), default="OTRO",nullable=False)
    # ACCION, COMEDIA, DRAMA, SCI-FI, FILOSOFIA, TERROR, MANGA, COCINA, LITERATURA
    ejemplares: Mapped[List["Ejemplar"]] = relationship("Ejemplar", back_populates="libro", cascade="all, delete-orphan")

class Ejemplar(Base):
    __tablename__ = "ejemplares"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    libro_id: Mapped[int] = mapped_column(ForeignKey("libros.id"), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), default="DISPONIBLE", nullable=False)  # DISPONIBLE, PRESTADO

    # Relación
    libro: Mapped["Libro"] = relationship("Libro", back_populates="ejemplares")

    # Índice
    __table_args__ = (
        Index("idx_libro_estado", "libro_id", "estado"),
    )
