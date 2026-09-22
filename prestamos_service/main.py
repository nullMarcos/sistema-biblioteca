import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text

from src.seed import seed_database
from src.database import engine, Base
from src.routers.socios import router as socios_router
from src.routers.prestamos import router as prestamos_router
from src.schemas import HealthResponse, DependencyStatus, ErrorResponse
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

# MANEJADORES GLOBALES DE EXCEPCIONES (ESQUEMA OPENAPI: {codigo, mensaje})

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Garantiza que cualquier HTTPException retorne la estructura {"codigo": ..., "mensaje": ...}."""
    if isinstance(exc.detail, dict) and "codigo" in exc.detail and "mensaje" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
            headers=exc.headers,
        )

    codigos_por_defecto = {
        400: "SOLICITUD_INVALIDA",
        401: "NO_AUTORIZADO",
        403: "PROHIBIDO",
        404: "NO_ENCONTRADO",
        409: "CONFLICTO",
        500: "ERROR_INTERNO",
        502: "ERROR_CATALOGO",
        503: "CATALOGO_NO_DISPONIBLE",
        504: "CATALOGO_TIMEOUT",
    }
    codigo = codigos_por_defecto.get(exc.status_code, "ERROR")
    mensaje = str(exc.detail) if exc.detail else "Ha ocurrido un error en la solicitud"

    return JSONResponse(
        status_code=exc.status_code,
        content={"codigo": codigo, "mensaje": mensaje},
        headers=exc.headers,
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Mapea errores de validación de esquema a HTTP 400 con formato {"codigo": ..., "mensaje": ...}."""
    mensajes = []
    for err in exc.errors():
        campo = " -> ".join(str(loc) for loc in err.get("loc", []))
        msg = err.get("msg", "inválido")
        mensajes.append(f"{campo}: {msg}")
    detalle_msg = "; ".join(mensajes) if mensajes else "Error de validación en la petición"

    return JSONResponse(
        status_code=400,
        content={"codigo": "ERROR_VALIDACION", "mensaje": detalle_msg},
    )

@app.exception_handler(grpc_client.CatalogoError)
async def catalogo_error_handler(request: Request, exc: grpc_client.CatalogoError):
    """Manejador de respaldo para excepciones de Catálogo que no hayan sido capturadas en routers."""
    if isinstance(exc, grpc_client.CircuitBreakerOpenError):
        status_code = 503
    elif isinstance(exc, grpc_client.CatalogoTimeoutError):
        status_code = 504
    elif isinstance(exc, grpc_client.CatalogoNoDisponibleError):
        status_code = 503
    elif isinstance(exc, (grpc_client.SinStockError, grpc_client.LiberacionFallidaError)):
        status_code = 409
    elif isinstance(exc, grpc_client.CatalogoNotFoundError):
        status_code = 404
    elif isinstance(exc, grpc_client.CatalogoInvalidArgumentError):
        status_code = 400
    else:
        status_code = 502

    return JSONResponse(
        status_code=status_code,
        content={"codigo": exc.codigo, "mensaje": exc.mensaje},
    )


app.include_router(socios_router)
app.include_router(prestamos_router)

# Endpoint de chequeo de salud
@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthResponse,
    responses={
        200: {"model": HealthResponse, "description": "Servicio y dependencias operativas"},
        503: {"model": HealthResponse, "description": "Una o más dependencias críticas no disponibles"},
    }
)
def health_check(response: Response):
    """
    Chequeo de salud integrado:
    - Verifica conexión a la base de datos SQLite local.
    - Verifica conectividad con el servicio gRPC de Catálogo.
    - Reporta el estado actual del Circuit Breaker (closed, open, half_open).
    """
    # 1. Comprobación de Base de Datos
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Health check - Error en base de datos: %s", e)
        db_status = "unreachable"

    # 2. Comprobación de Servicio gRPC Catálogo
    grpc_info = grpc_client.verificar_salud_grpc(timeout=1.0)
    grpc_status = grpc_info.get("status", "unreachable")

    # 3. Estado del Circuit Breaker
    cb_status = grpc_client.catalogo_circuit_breaker.get_state()

    # Estado global del servicio
    is_healthy = (db_status == "ok" and grpc_status == "ok" and cb_status != "open")

    if not is_healthy:
        response.status_code = 503
        global_status = "degraded" if db_status == "ok" else "unhealthy"
    else:
        response.status_code = 200
        global_status = "ok"

    return HealthResponse(
        status=global_status,
        service="prestamos",
        dependencies=DependencyStatus(
            database=db_status,
            catalogo_grpc=grpc_status,
            circuit_breaker=cb_status,
        )
    )
