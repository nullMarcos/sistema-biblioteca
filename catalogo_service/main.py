import os
import logging
from concurrent import futures
import grpc
from src.seed import seed_database
from src.database import engine, Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def inicializar_bd():
    """Crea las tablas en SQLite si aún no existen."""
    Base.metadata.create_all(bind=engine)
    logger.info("Base de datos de Catálogo inicializada.")

def serve():
    inicializar_bd()
    seed_database()

    puerto = os.getenv("GRPC_PORT", "50051")
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    servidor.add_insecure_port(f"[::]:{puerto}")
    servidor.start()
    logger.info(f"Servidor gRPC de Catálogo iniciado en el puerto {puerto}")
    servidor.wait_for_termination()

if __name__ == "__main__":
    serve()
