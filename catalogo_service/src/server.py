"""
Implementacion de la clase CatalogoServicer para atender las solicitudes gRPC
del servicio de Catalogo de la biblioteca.
"""

import os
import sys
import logging
import grpc
from sqlalchemy.orm import Session

PROTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protos"))
if PROTOS_DIR not in sys.path:
    sys.path.insert(0, PROTOS_DIR)

import catalogo_pb2
import catalogo_pb2_grpc
from src.database import SessionLocal
from src.models import Libro, Ejemplar

logger = logging.getLogger(__name__)


class CatalogoServicer(catalogo_pb2_grpc.CatalogoServicer):
    """Servidor gRPC que maneja el inventario de libros y reserva/liberacion de ejemplares."""

    def ReservarEjemplar(self, request, context):
        """
        Reserva un ejemplar disponible de un libro especifico.
        Transaccion atomica: busca la primera copia con estado DISPONIBLE y la marca como PRESTADO.
        """
        session: Session = SessionLocal()
        try:
            libro = session.get(Libro, request.libro_id)
            if libro is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Libro no encontrado")
                return catalogo_pb2.ReservaResponse()

            ejemplar = (
                session.query(Ejemplar)
                .filter(Ejemplar.libro_id == request.libro_id, Ejemplar.estado == "DISPONIBLE")
                .with_for_update()
                .first()
            )

            if not ejemplar:
                logger.warning(f"No hay ejemplares disponibles para el libro_id={request.libro_id}")
                return catalogo_pb2.ReservaResponse(
                    ejemplar_id=0,
                    exito=False,
                    motivo="No hay ejemplares disponibles para el libro solicitado."
                )

            ejemplar.estado = "PRESTADO"
            session.commit()

            logger.info(f"Ejemplar reservado exitosamente: ejemplar_id={ejemplar.id} para libro_id={request.libro_id}")
            return catalogo_pb2.ReservaResponse(
                ejemplar_id=ejemplar.id,
                exito=True,
                motivo=""
            )
        except Exception as e:
            session.rollback()
            logger.error(f"Error inesperado al reservar ejemplar para libro_id={request.libro_id}: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Error interno al procesar la reserva")
            return catalogo_pb2.ReservaResponse()
        finally:
            session.close()

    def LiberarEjemplar(self, request, context):
        """
        Libera un ejemplar previamente prestado, cambiándolo de PRESTADO a DISPONIBLE.
        """
        session: Session = SessionLocal()
        try:
            ejemplar = (
                session.query(Ejemplar)
                .filter(Ejemplar.id == request.ejemplar_id)
                .with_for_update()
                .first()
            )

            if not ejemplar:
                logger.warning(f"Intento de liberar un ejemplar inexistente: ejemplar_id={request.ejemplar_id}")
                return catalogo_pb2.LiberarResponse(
                    exito=False,
                    motivo="El ejemplar solicitado no existe."
                )

            if ejemplar.estado == "DISPONIBLE":
                logger.warning(f"El ejemplar_id={request.ejemplar_id} ya se encontraba DISPONIBLE")
                return catalogo_pb2.LiberarResponse(
                    exito=False,
                    motivo="El ejemplar ya se encuentra disponible."
                )

            ejemplar.estado = "DISPONIBLE"
            session.commit()

            logger.info(f"Ejemplar liberado exitosamente: ejemplar_id={request.ejemplar_id}")
            return catalogo_pb2.LiberarResponse(
                exito=True,
                motivo=""
            )
        except Exception as e:
            session.rollback()
            logger.error(f"Error inesperado al liberar ejemplar_id={request.ejemplar_id}: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Error interno al procesar la liberación")
            return catalogo_pb2.LiberarResponse()
        finally:
            session.close()

    def ConsultarDisponibilidad(self, request, context):
        """
        Consulta la cantidad total de ejemplares y la cantidad disponible para un libro.
        """
        session: Session = SessionLocal()
        try:
            libro = session.query(Libro).filter(Libro.id == request.libro_id).first()
            if not libro:
                return catalogo_pb2.ConsultaResponse(
                    libro_id=request.libro_id,
                    ejemplares_disponibles=0,
                    total_ejemplares=0,
                    titulo=""
                )

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
