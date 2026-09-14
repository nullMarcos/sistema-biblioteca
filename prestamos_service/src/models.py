from typing import List, Optional
from datetime import datetime, date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Index, Integer, DateTime, Date, func
from src.database import Base

class Socio(Base):
    __tablename__ = "socios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    correo: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    prestamos: Mapped[List["Prestamo"]] = relationship("Prestamo", back_populates="socio")

class Prestamo(Base):
    __tablename__ = "prestamos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    socio_id: Mapped[int] = mapped_column(ForeignKey("socios.id"), nullable=False)

    # ID lógico de Catálogo: No lleva ForeignKey ni relationship porque es del servicio de "Catálogo" (otro servicio)
    libro_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ejemplar_id: Mapped[int] = mapped_column(Integer, nullable=False)

    fecha_prestamo: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fecha_limite: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_devolucion: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="ACTIVO", nullable=False)

    # Relación
    socio: Mapped["Socio"] = relationship("Socio", back_populates="prestamos")

    # Índice
    __table_args__ = (
        Index("idx_socio_prestamo_activo", "socio_id", "estado"),
    )
