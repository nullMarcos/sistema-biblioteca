import os
import asyncio
import logging
from decimal import Decimal
from contextlib import asynccontextmanager
from typing import Optional, List

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text, or_
from sqlalchemy.orm import Session

from src.database import engine, Base, get_db
from src import models
from src.schemas import (
    SancionSocioResponse,
    BloqueoOut,
    MultaOut,
    HealthResponse,
    DependencyStatus,
    ChaosState,
    ErrorResponse,
)
from src.seed import seed_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sanciones_service")

# --- ESTADO Y MÓDULO DE CAOS ---
chaos_config = {
    "delay_seconds": 0.0,
    "fault_status": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: se ejecuta al arrancar el contenedor o servidor."""
    logger.info("Inicializando servicio de Sanciones...")
    Base.metadata.create_all(bind=engine)
    seed_database()
    logger.info("Base de datos y tablas de Sanciones listas.")
    yield
    logger.info("Cerrando servicio de Sanciones...")


app = FastAPI(
    title="API de Sanciones - Red de Bibliotecas",
    description="Microservicio de verificación de sanciones, multas y bloqueos de socios con módulo de inyección de caos",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


# --- MIDDLEWARE DE CAOS ---
@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    """
    Aplica latencia artificial o fallas forzadas controladas si el módulo de caos está activo.
    Excluye rutas de control (/chaos), salud (/health) y documentación para permitir diagnóstico.
    """
    path = request.url.path
    is_excluded = (
        path.startswith("/chaos")
        or path == "/health"
        or path.startswith("/docs")
        or path.startswith("/openapi")
    )

    if not is_excluded:
        # 1. Inducir retraso artificial (latencia)
        delay = chaos_config.get("delay_seconds", 0.0)
        if delay > 0:
            logger.warning("[CAOS] Induciendo latencia artificial de %.2fs en %s", delay, path)
            await asyncio.sleep(delay)

        # 2. Inducir falla HTTP controlada (ej. 500)
        fault_code = chaos_config.get("fault_status")
        if fault_code:
            logger.warning("[CAOS] Induciendo respuesta HTTP %s en %s", fault_code, path)
            return JSONResponse(
                status_code=fault_code,
                content={
                    "codigo": "FALLA_CONTROLADA_CAOS",
                    "mensaje": f"Falla forzada por módulo de caos activo (HTTP {fault_code})",
                },
            )

    return await call_next(request)


# --- MANEJADORES GLOBALES DE EXCEPCIONES ---
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Estandariza los errores HTTP al formato {"codigo": ..., "mensaje": ...}."""
    if isinstance(exc.detail, dict) and "codigo" in exc.detail and "mensaje" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)

    codigos = {
        400: "SOLICITUD_INVALIDA",
        404: "NO_ENCONTRADO",
        409: "CONFLICTO",
        500: "ERROR_INTERNO",
    }
    codigo = codigos.get(exc.status_code, "ERROR")
    mensaje = str(exc.detail) if exc.detail else "Ha ocurrido un error en la solicitud"

    return JSONResponse(
        status_code=exc.status_code,
        content={"codigo": codigo, "mensaje": mensaje},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Mapea errores de validación Pydantic a HTTP 400 estandarizado."""
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


# --- RUTAS DE CONTROL DE CAOS ---
@app.api_route("/chaos/delay", methods=["GET", "POST"], tags=["Caos"])
def set_chaos_delay(seconds: float = Query(..., ge=0.0, description="Segundos de latencia artificial a inducir")):
    """Configura latencia artificial en llamadas al servicio de Sanciones."""
    chaos_config["delay_seconds"] = float(seconds)
    logger.info("[CAOS] Configurada latencia artificial: %ss", seconds)
    return {"mensaje": f"Latencia de {seconds}s configurada", "caos": chaos_config}


@app.api_route("/chaos/fault", methods=["GET", "POST"], tags=["Caos"])
def set_chaos_fault(status: int = Query(500, description="Código de estado HTTP de falla a retornar (ej. 500)")):
    """Configura una falla HTTP forzada (ej. 500) en llamadas al servicio."""
    chaos_config["fault_status"] = int(status)
    logger.info("[CAOS] Configurada falla forzada HTTP %s", status)
    return {"mensaje": f"Falla HTTP {status} configurada", "caos": chaos_config}


@app.api_route("/chaos/reset", methods=["GET", "POST"], tags=["Caos"])
def reset_chaos():
    """Restaura el comportamiento normal del servicio, eliminando retardos y fallas forzadas."""
    chaos_config["delay_seconds"] = 0.0
    chaos_config["fault_status"] = None
    logger.info("[CAOS] Módulo de caos restablecido a normalidad.")
    return {"mensaje": "Módulo de caos restablecido", "caos": chaos_config}


@app.get("/chaos/status", tags=["Caos"], response_model=ChaosState)
def get_chaos_status():
    """Consulta el estado actual de inyección de fallas y retardos."""
    return ChaosState(
        delay_seconds=chaos_config["delay_seconds"],
        fault_status=chaos_config["fault_status"],
    )


# --- ENDPOINTS DE NEGOCIO: SANCIONES ---
@app.get(
    "/v1/sanciones/socios/{identificador}",
    response_model=SancionSocioResponse,
    tags=["Sanciones"],
    responses={
        200: {"model": SancionSocioResponse, "description": "Detalle de sanciones del socio"},
        404: {"model": ErrorResponse, "description": "Socio no encontrado"},
    },
)
def consultar_sanciones_socio(identificador: str, db: Session = Depends(get_db)):
    """
    Consulta si un socio tiene sanciones activas (bloqueos o multas pendientes).
    El parámetro `identificador` admite tanto el RUT ('11.111.111-1') como el ID numérico (ej. '1').
    """
    socio = None
    if identificador.isdigit():
        socio = db.query(models.Socio).filter(
            or_(models.Socio.id == int(identificador), models.Socio.rut == identificador)
        ).first()
    else:
        socio = db.query(models.Socio).filter(models.Socio.rut == identificador).first()

    if not socio:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "SOCIO_NO_ENCONTRADO",
                "mensaje": f"No se encontró un socio con identificador '{identificador}'",
            },
        )

    # Filtrar bloqueos y multas vigentes
    bloqueos_activos = [b for b in socio.bloqueos if b.estado == "ACTIVO"]
    multas_pendientes = [m for m in socio.multas if m.estado == "PENDIENTE"]
    monto_total = sum((m.monto or Decimal(0)) for m in multas_pendientes)

    tiene_sancion = len(bloqueos_activos) > 0 or len(multas_pendientes) > 0

    return SancionSocioResponse(
        socio_id=socio.id,
        rut=socio.rut,
        nombre=socio.nombre,
        tiene_sancion=tiene_sancion,
        bloqueado=len(bloqueos_activos) > 0,
        multas_pendientes=len(multas_pendientes),
        monto_total_multas=float(monto_total),
        bloqueos_activos=[BloqueoOut.model_validate(b) for b in bloqueos_activos],
        multas_activas=[MultaOut.model_validate(m) for m in multas_pendientes],
    )


@app.get(
    "/v1/sanciones/socios",
    response_model=List[SancionSocioResponse],
    tags=["Sanciones"],
)
def listar_sanciones_socios(rut: Optional[str] = None, db: Session = Depends(get_db)):
    """Lista todos los socios y el resumen de sus sanciones, con filtro opcional por RUT."""
    query = db.query(models.Socio)
    if rut is not None:
        query = query.filter(models.Socio.rut == rut)
    socios = query.all()
    resultado = []
    for s in socios:
        b_activos = [b for b in s.bloqueos if b.estado == "ACTIVO"]
        m_pendientes = [m for m in s.multas if m.estado == "PENDIENTE"]
        monto_total = sum((m.monto or Decimal(0)) for m in m_pendientes)
        resultado.append(
            SancionSocioResponse(
                socio_id=s.id,
                rut=s.rut,
                nombre=s.nombre,
                tiene_sancion=len(b_activos) > 0 or len(m_pendientes) > 0,
                bloqueado=len(b_activos) > 0,
                multas_pendientes=len(m_pendientes),
                monto_total_multas=float(monto_total),
                bloqueos_activos=[BloqueoOut.model_validate(b) for b in b_activos],
                multas_activas=[MultaOut.model_validate(m) for m in m_pendientes],
            )
        )
    return resultado


# --- CHEQUEO DE SALUD ---
@app.get(
    "/health",
    tags=["Health"],
    response_model=HealthResponse,
    responses={
        200: {"model": HealthResponse, "description": "Servicio de sanciones operativo"},
        503: {"model": HealthResponse, "description": "Base de datos no disponible"},
    },
)
def health_check(response: Response):
    """Verifica el estado operativo del microservicio de Sanciones y su conexión SQLite."""
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Health check - Falló conexión a base de datos de Sanciones: %s", e)
        db_status = "unreachable"

    if db_status != "ok":
        response.status_code = 503
        return HealthResponse(
            status="unhealthy",
            service="sanciones",
            dependencies=DependencyStatus(database=db_status),
        )

    return HealthResponse(
        status="ok",
        service="sanciones",
        dependencies=DependencyStatus(database=db_status),
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
