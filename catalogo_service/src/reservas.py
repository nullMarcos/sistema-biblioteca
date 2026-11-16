"""Identidad durable y propiedad de reservas. El caller posee BEGIN IMMEDIATE."""

import json

import grpc
from src.models import Libro, Ejemplar, ReservaOperacion, LiberacionOperacion
import catalogo_pb2 as pb


class ErrorOperacion(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo


def respuesta(registro):
    return pb.ReservaResponse(ejemplar_id=registro.ejemplar_id or 0,
        exito=registro.ejemplar_id is not None,
        motivo=registro.motivo, estado=registro.estado)


def reservar(db, request):
    previa = db.get(ReservaOperacion, request.operacion_id)
    if previa:
        if previa.libro_id != request.libro_id:
            raise ErrorOperacion(grpc.StatusCode.ALREADY_EXISTS, "Identidad reutilizada con otro libro")
        return respuesta(previa)
    registro = ReservaOperacion(operacion_id=request.operacion_id, libro_id=request.libro_id,
        estado="SIN_STOCK", motivo="No hay ejemplares disponibles para el libro solicitado.")
    if db.get(Libro, request.libro_id) is None:
        registro.estado, registro.motivo = "NO_ENCONTRADO", "Libro no encontrado"
    else:
        ejemplar = db.query(Ejemplar).filter_by(libro_id=request.libro_id, estado="DISPONIBLE").order_by(Ejemplar.id).first()
        if ejemplar:
            ejemplar.estado = "PRESTADO"
            registro.ejemplar_id = ejemplar.id
            registro.estado, registro.motivo = "RESERVADA", ""
    db.add(registro)
    return respuesta(registro)


def liberar(db, request, cancelar=False):
    payload = {"tipo": "CANCELAR" if cancelar else "LIBERAR", "reserva_id": request.reserva_id,
        "libro_id" if cancelar else "ejemplar_id": request.libro_id if cancelar else request.ejemplar_id}
    normalizado = json.dumps(payload, sort_keys=True)
    previa = db.get(LiberacionOperacion, request.operacion_id)
    if previa:
        if previa.payload_json != normalizado:
            raise ErrorOperacion(grpc.StatusCode.ALREADY_EXISTS, "Identidad de liberación con otro payload")
        return pb.LiberarResponse(exito=True, estado=previa.estado)
    reserva = db.get(ReservaOperacion, request.reserva_id)
    if reserva is None:
        if not cancelar:
            raise ErrorOperacion(grpc.StatusCode.FAILED_PRECONDITION, "No existe la reserva propietaria; requiere conciliación")
        reserva = ReservaOperacion(operacion_id=request.reserva_id, libro_id=request.libro_id,
            estado="CANCELADA", motivo="Cancelación anterior a la reserva")
        db.add(reserva)
    elif (cancelar and reserva.libro_id != request.libro_id) or (
            not cancelar and reserva.ejemplar_id != request.ejemplar_id):
        raise ErrorOperacion(grpc.StatusCode.ALREADY_EXISTS, "Payload distinto de la reserva propietaria")
    if reserva.estado == "RESERVADA":
        ejemplar = db.get(Ejemplar, reserva.ejemplar_id)
        if ejemplar is None or ejemplar.estado != "PRESTADO":
            raise ErrorOperacion(grpc.StatusCode.FAILED_PRECONDITION, "Inventario inconsistente con la reserva; requiere conciliación")
        ejemplar.estado = "DISPONIBLE"
        reserva.estado = "CANCELADA" if cancelar else "LIBERADA"
    # Una reserva terminal nunca libera el ejemplar asignado a otra operación.
    estado = "CANCELADA" if cancelar else "LIBERADA"
    db.add(LiberacionOperacion(operacion_id=request.operacion_id, payload_json=normalizado, estado=estado))
    return pb.LiberarResponse(exito=True, estado=estado)
