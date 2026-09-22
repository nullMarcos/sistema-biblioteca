import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.seed import seed_database
from src.database import engine, Base
from src.routers.socios import router as socios_router
from src.routers.prestamos import router as prestamos_router
from src import grpc_client


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: se ejecuta al arrancar el contenedor."""
    Base.metadata.create_all(bind=engine)
    seed_database()
    logger.info("Base de datos de Préstamos inicializada.")
    yield
    # Lógica de apagado (cierre de canales gRPC si aplica)
    grpc_client.cerrar_canal()
    logger.info("Cerrando servicio de Préstamos...")

app = FastAPI(
    title="API de Préstamos - Red de Bibliotecas",
    description="Servicio público REST para la gestión de socios y préstamos",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json"
)

app.include_router(socios_router)
app.include_router(prestamos_router)

# Endpoint de chequeo básico de salud
@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "prestamos"}
