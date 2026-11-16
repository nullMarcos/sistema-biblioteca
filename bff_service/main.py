from contextlib import asynccontextmanager
import logging
import re
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import Configuracion
from src.errores import FalloDependencia
from src.identity import verificar_api_key
from src.schemas import ErrorResponse, HealthOut

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("bff")


def crear_app(config=None, recursos_factory=None):
    config = config or Configuracion()

    @asynccontextmanager
    async def lifespan(app):
        from src.clients import Recursos
        async with (recursos_factory or Recursos)(config) as recursos:
            app.state.recursos = recursos
            yield

    app = FastAPI(title="BFF web de Biblioteca", version="1.0.0", lifespan=lifespan,
        description="Composición web detrás de Gateway. JWT en la entrada pública; API Key e identidad técnica en esta interfaz interna.",
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
            404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
            500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
            504: {"model": ErrorResponse}, "default": {"model": ErrorResponse}})
    app.state.config = config

    @app.middleware("http")
    async def correlacion(request, call_next):
        recibido = request.headers.get("X-Request-Id", "")
        request.state.request_id = recibido if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", recibido) else str(uuid.uuid4())
        inicio = time.monotonic()
        respuesta = await call_next(request)
        respuesta.headers["X-Request-Id"] = request.state.request_id
        clave = request.headers.get("Idempotency-Key", "")
        if request.url.path == "/bff/web/solicitar-prestamo" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", clave):
            respuesta.headers["Idempotency-Key"] = clave
        logger.info("servicio=bff operacion=%s request_id=%s resultado=%s duracion_ms=%.3f",
            request.method, request.state.request_id, respuesta.status_code, (time.monotonic() - inicio) * 1000)
        return respuesta

    @app.exception_handler(StarletteHTTPException)
    async def error_http(request, exc):
        detalle = exc.detail if isinstance(exc.detail, dict) else {"codigo": "ERROR_HTTP", "mensaje": str(exc.detail)}
        return JSONResponse(detalle, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def error_validacion(request, exc):
        return JSONResponse({"codigo": "ERROR_VALIDACION", "mensaje": "Formato de solicitud inválido"}, status_code=400)

    @app.exception_handler(FalloDependencia)
    async def error_dependencia(request, exc):
        return JSONResponse({"codigo": exc.codigo, "mensaje": exc.mensaje}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def error_interno(request, exc):
        logger.error("servicio=bff resultado=ERROR_INTERNO tipo=%s", type(exc).__name__)
        return JSONResponse({"codigo": "ERROR_INTERNO", "mensaje": "Error interno del BFF"}, status_code=500,
            headers={"X-Request-Id": getattr(request.state, "request_id", "")})

    @app.get("/health", response_model=HealthOut, tags=["Health"])
    async def health():
        return HealthOut()

    @app.get("/internal/metricas", dependencies=[Depends(verificar_api_key)], tags=["Diagnostico"])
    async def metricas(request: Request):
        return request.app.state.recursos.metricas()

    from src.routers import router
    app.include_router(router)
    return app


app = crear_app()
