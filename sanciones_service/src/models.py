from decimal import Decimal
from typing import List
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Integer, DateTime, Numeric, func
from src.database import Base

class Socio(Base):
    """
    Representa la entidad que tiene sanciones dentro del sistema
    """
    __tablename__ = "socios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    correo: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Relaciones
    bloqueos: Mapped[List["Bloqueo"]] = relationship("Bloqueo", back_populates="socio")
    multas: Mapped[List["Multa"]] = relationship("Multa", back_populates="socio")

class Bloqueo(Base):
    """
    Representa los bloqueos que tiene un socio
    """
    __tablename__ = "bloqueos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    socio_id: Mapped[int] = mapped_column(ForeignKey("socios.id"), nullable=False)
    motivo: Mapped[str] = mapped_column(String(255), nullable=False)
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True) # null = indefinido
    estado: Mapped[str] = mapped_column(String(20), default="ACTIVO", nullable=False) # Valores: ACTIVO, LEVANTADO
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relación
    socio: Mapped["Socio"] = relationship("Socio", back_populates="bloqueos")

class Multa(Base):
    """
    Representa las multas que tiene un socio
    """
    __tablename__ = "multas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    socio_id: Mapped[int] = mapped_column(ForeignKey("socios.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), default="ATRASO", nullable=False) # Valores: ATRASO, DANO, PERDIDA

    # ID lógico de Catálogo: No lleva ForeignKey ni relationship porque es del servicio de "Catálogo" (otro servicio)
    libro_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ejemplar_id: Mapped[int] = mapped_column(Integer, nullable=False)

    dias_atraso: Mapped[int | None] = mapped_column(Integer, nullable=True) # null si tipo no es ATRASO
    monto: Mapped[Decimal | None] = mapped_column(Numeric(10,2))
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", nullable=False) # Valores: PENDIENTE, PAGADA, ANULADA
    fecha_emision: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_pago: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True) # null si no pagada
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relación
    socio: Mapped["Socio"] = relationship("Socio", back_populates="multas")
