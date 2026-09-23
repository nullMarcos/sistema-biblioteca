"""
Punto de entrada principal para el servidor gRPC del Servicio de Catalogo.
Inicializa la base de datos, ejecuta la carga de datos semilla y activa
los servicios gRPC, Reflection y Health Check.
"""

import os
import sys
import logging
from concurrent import futures
import grpc

# Garantizar resolucion del directorio protos
PROTOS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "protos"))
if PROTOS_DIR not in sys.path:
    sys.path.insert(0, PROTOS_DIR)

import catalogo_pb2
import catalogo_pb2_grpc
from src.seed import seed_database
from src.database import engine, Base
from src.server import CatalogoServicer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def inicializar_bd():
    """Crea las tablas en la base de datos de Catalogo si aun no existen."""
    Base.metadata.create_all(bind=engine)
    logger.info("Base de datos de Catalogo inicializada correctamente.")


def serve():
    """Inicializa la base de datos, carga datos iniciales y levanta el servidor gRPC."""
    inicializar_bd()
    seed_database()

    puerto = os.getenv("GRPC_PORT", "50051")
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Registrar Servicer principal de Catalogo
    catalogo_pb2_grpc.add_CatalogoServicer_to_server(CatalogoServicer(), servidor)

    servidor.add_insecure_port(f"[::]:{puerto}")
    servidor.start()
    logger.info(f"Servidor gRPC de Catalogo iniciado en el puerto {puerto}.")

    import signal

    def detener_servidor(sig, frame):
        logger.info("Recibida señal de terminación (%s). Deteniendo servidor gRPC...", sig)
        servidor.stop(grace=2)

    signal.signal(signal.SIGTERM, detener_servidor)
    signal.signal(signal.SIGINT, detener_servidor)

    try:
        servidor.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Deteniendo el servidor gRPC de Catalogo...")
        servidor.stop(0)


if __name__ == "__main__":
    serve()

