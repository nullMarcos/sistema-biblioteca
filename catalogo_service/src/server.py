"""
Implementacion de la clase CatalogoServicer para atender las solicitudes gRPC
del servicio de Catalogo de la biblioteca.
"""

import os
import sys
import logging
import sqlite3
import re
import grpc
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

PROTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protos"))
if PROTOS_DIR not in sys.path:
    sys.path.insert(0, PROTOS_DIR)

import catalogo_pb2
import catalogo_pb2_grpc
from src.database import SessionLocal, iniciar_escritura
from src.models import Libro, Ejemplar, ReservaOperacion
from src import reservas

logger = logging.getLogger(__name__)


def error_sqlite(context, exc):
    codigo = getattr(exc.orig, "sqlite_errorcode", 0) & 255
    if codigo in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        context.set_code(grpc.StatusCode.UNAVAILABLE)
        context.set_details("SQLITE_BUSY: espera acotada por el escritor de Catálogo")
    else:
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details("Error de persistencia en Catálogo")


def validar_id(identificador, context):
    if identificador <= 0:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El identificador debe ser positivo")


def validar_operacion(identificador, context):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", identificador):
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Identidad de operación obligatoria e inválida")


class CatalogoServicer(catalogo_pb2_grpc.CatalogoServicer):
    """Servidor gRPC que maneja el inventario de libros y reserva/liberacion de ejemplares."""

    def _escritura(self, request, context, respuesta, accion):
        session: Session = SessionLocal()
        try:
            iniciar_escritura(session)
            resultado = accion(session, request)
            session.commit()
            if resultado.estado == "NO_ENCONTRADO":
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Libro no encontrado")
            return resultado
        except reservas.ErrorOperacion as e:
            session.rollback()
            context.set_code(e.codigo)
            context.set_details(str(e))
            return respuesta()
        except OperationalError as e:
            session.rollback()
            error_sqlite(context, e)
            return respuesta()
        except Exception:
            session.rollback()
            logger.exception("Error al persistir operación de Catálogo")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Error interno al persistir la operación")
            return respuesta()
        finally:
            session.close()

    def ReservarEjemplar(self, request, context):
        validar_id(request.libro_id, context)
        validar_operacion(request.operacion_id, context)
        return self._escritura(request, context, catalogo_pb2.ReservaResponse, reservas.reservar)

    def LiberarEjemplar(self, request, context):
        validar_id(request.ejemplar_id, context)
        validar_operacion(request.operacion_id, context)
        validar_operacion(request.reserva_id, context)
        return self._escritura(request, context, catalogo_pb2.LiberarResponse, reservas.liberar)

    def CancelarReserva(self, request, context):
        validar_id(request.libro_id, context)
        validar_operacion(request.operacion_id, context)
        validar_operacion(request.reserva_id, context)
        return self._escritura(request, context, catalogo_pb2.LiberarResponse,
            lambda db, req: reservas.liberar(db, req, cancelar=True))

    def ConsultarOperacion(self, request, context):
        validar_operacion(request.operacion_id, context)
        with SessionLocal() as session:
            registro = session.get(ReservaOperacion, request.operacion_id)
            if registro is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Operación no encontrada")
                return catalogo_pb2.ReservaResponse()
            return reservas.respuesta(registro)

    def ConsultarDisponibilidad(self, request, context):
        """
        Consulta la cantidad total de ejemplares y la cantidad disponible para un libro.
        """
        validar_id(request.libro_id, context)
        session: Session = SessionLocal()
        try:
            libro = session.query(Libro).filter(Libro.id == request.libro_id).first()
            if not libro:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Libro no encontrado")
                return catalogo_pb2.ConsultaResponse()

            total = len(libro.ejemplares)
            disponibles = sum(1 for e in libro.ejemplares if e.estado == "DISPONIBLE")

            return catalogo_pb2.ConsultaResponse(
                libro_id=libro.id,
                ejemplares_disponibles=disponibles,
                total_ejemplares=total,
                titulo=libro.titulo
            )
        finally:
            session.close()

    def ListarCatalogo(self, request, context):
        """
        Lista los libros del catalogo con opciones de filtrado por genero y disponibilidad.
        """
        session: Session = SessionLocal()
        try:
            query = session.query(Libro)

            if request.genero:
                query = query.filter(Libro.genero == request.genero.upper())

            libros = query.all()
            libros_dtos = []

            for libro in libros:
                total = len(libro.ejemplares)
                disponibles = sum(1 for e in libro.ejemplares if e.estado == "DISPONIBLE")

                if request.solo_disponibles and disponibles == 0:
                    continue

                ejemplares_dtos = [
                    catalogo_pb2.EjemplarDTO(
                        id=e.id,
                        libro_id=e.libro_id,
                        estado=e.estado
                    ) for e in libro.ejemplares
                ]

                libro_dto = catalogo_pb2.LibroDTO(
                    id=libro.id,
                    titulo=libro.titulo,
                    autor=libro.autor,
                    genero=libro.genero,
                    total_ejemplares=total,
                    ejemplares_disponibles=disponibles,
                    ejemplares=ejemplares_dtos
                )
                libros_dtos.append(libro_dto)

            return catalogo_pb2.CatalogoResponse(libros=libros_dtos)
        finally:
            session.close()

    def ObtenerLibro(self, request, context):
        """
        Obtiene la informacion detallada de un libro especifico por su ID.
        """
        validar_id(request.libro_id, context)
        session: Session = SessionLocal()
        try:
            libro = session.query(Libro).filter(Libro.id == request.libro_id).first()
            if not libro:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Libro no encontrado")
                return catalogo_pb2.LibroDTO()

            total = len(libro.ejemplares)
            disponibles = sum(1 for e in libro.ejemplares if e.estado == "DISPONIBLE")

            ejemplares_dtos = [
                catalogo_pb2.EjemplarDTO(
                    id=e.id,
                    libro_id=e.libro_id,
                    estado=e.estado
                ) for e in libro.ejemplares
            ]

            return catalogo_pb2.LibroDTO(
                id=libro.id,
                titulo=libro.titulo,
                autor=libro.autor,
                genero=libro.genero,
                total_ejemplares=total,
                ejemplares_disponibles=disponibles,
                ejemplares=ejemplares_dtos
            )
        finally:
            session.close()
