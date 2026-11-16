from typing import List, Optional
from datetime import datetime, date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, String, Index, Integer, DateTime, Date, Text, Boolean, Float, func
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
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    cupo_reservado: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    reserva_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    compensacion_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    accion_pendiente: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sanciones_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0, server_default="0", nullable=False)
    recuperacion_intentos: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    proximo_intento: Mapped[float] = mapped_column(Float, default=0, server_default="0", nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_saga_estado", "estado"),
    )


class DevolucionOperacion(Base):
    __tablename__ = "devolucion_operaciones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    operacion_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prestamo_id: Mapped[int] = mapped_column(ForeignKey("prestamos.id"), unique=True, nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reserva_id: Mapped[str] = mapped_column(String(128), nullable=False)
    liberacion_id: Mapped[str] = mapped_column(String(128), nullable=False)
    estado_final: Mapped[str] = mapped_column(String(20), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), default="PENDIENTE", nullable=False)
    accion_pendiente: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    respuesta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_detalle: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0, server_default="0", nullable=False)
    recuperacion_intentos: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    proximo_intento: Mapped[float] = mapped_column(Float, default=0, server_default="0", nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
