from typing import List, Optional
from datetime import datetime, date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Index, Integer, DateTime, Date, Text, func
from src.database import Base

class Socio(Base):
    """
    Representa la entidad que solicita los préstamos dentro del sistema
    """
    __tablename__ = "socios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    correo: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    prestamos: Mapped[List["Prestamo"]] = relationship("Prestamo", back_populates="socio")

class Prestamo(Base):
    """
    Representa la transaccion
    """
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

class SagaTransaccion(Base):
    """
    Registro persistente de transacciones distribuidas (Saga) e Idempotencia.
    Permite auditar el estado del flujo, coordinar compensaciones y responder peticiones repetidas.
    """
    __tablename__ = "saga_transacciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    saga_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    socio_id: Mapped[int] = mapped_column(Integer, nullable=False)
    libro_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ejemplar_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prestamo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estado: Mapped[str] = mapped_column(String(30), default="INICIADA", nullable=False)
    paso_actual: Mapped[str] = mapped_column(String(50), default="INICIO", nullable=False)
    respuesta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_saga_estado", "estado"),
    )
