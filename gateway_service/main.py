import os
import time
import math
import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from collections import defaultdict, deque

logger = logging.getLogger("gateway")

class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str


@asynccontextmanager
async def lifespan(app):
    global client
    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, trust_env=False)
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="API Gateway Biblioteca", description="Entrada pública: JWT, limitación de tasa, BFF web /bff/web y proxy REST /api/v1.",
    lifespan=lifespan,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
        "default": {"model": ErrorResponse}})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    codigos = {400: "SOLICITUD_INVALIDA", 401: "NO_AUTORIZADO", 403: "PROHIBIDO", 404: "NO_ENCONTRADO", 429: "LIMITE_EXCEDIDO"}
    return JSONResponse(status_code=exc.status_code, headers=exc.headers,
        content={"codigo": codigos.get(exc.status_code, "ERROR_HTTP"), "mensaje": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"codigo": "ERROR_VALIDACION", "mensaje": "Formato de solicitud inválido"})


@app.exception_handler(Exception)
async def error_inesperado_handler(request: Request, exc: Exception):
    logger.error("Error no controlado en %s", request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"codigo": "ERROR_INTERNO", "mensaje": "Error interno del servicio"},
        headers={"X-Request-Id": getattr(request.state, "request_id", "")})

# --- Configuracion ---
PRESTAMOS_URL = os.getenv("PRESTAMOS_URL", "http://prestamos:8000")
BFF_URL = os.getenv("BFF_URL", "http://bff:8000")
API_KEY_INTERNA = os.getenv("API_KEY", "")
JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_cambiar")
JWT_ALGORITMO = "HS256"
JWT_DURACION_SEG = 3600
LIMITE_PETICIONES = int(os.getenv("RATE_LIMIT", "20"))
VENTANA_SEG = int(os.getenv("RATE_WINDOW", "60"))
UPSTREAM_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT_SEGS", "10"))
SAGA_TIMEOUT = float(os.getenv("SAGA_TIMEOUT_SEGS", "8"))
BFF_POST_TIMEOUT = float(os.getenv("BFF_POST_TIMEOUT_SEGS", "9"))
BFF_RESUMEN_TIMEOUT = float(os.getenv("BFF_RESUMEN_TIMEOUT_SEGS", "3"))
if (any(not math.isfinite(t) or t <= 0 for t in (UPSTREAM_TIMEOUT, SAGA_TIMEOUT, BFF_POST_TIMEOUT, BFF_RESUMEN_TIMEOUT))
        or UPSTREAM_TIMEOUT <= max(SAGA_TIMEOUT + 1, BFF_POST_TIMEOUT + 0.5, BFF_RESUMEN_TIMEOUT + 0.5)):
    raise RuntimeError("GATEWAY_TIMEOUT_SEGS debe dejar margen sobre Saga (+1 s) y BFF (+0,5 s)")
SOCIO1_SOCIO_ID = int(os.getenv("SOCIO1_SOCIO_ID", "1"))
if not 0 < SOCIO1_SOCIO_ID <= 9223372036854775807:
    raise RuntimeError("SOCIO1_SOCIO_ID debe identificar un socio positivo de Préstamos")

peticiones_por_ip = defaultdict(deque)

RUTAS = {"prestamos": PRESTAMOS_URL, "socios": PRESTAMOS_URL}
METODOS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CABECERAS_IGNORADAS = {"host", "content-length", "authorization", "x-api-key", "x-request-id", "connection",
    "transfer-encoding", "keep-alive", "proxy-authorization", "proxy-authenticate", "te", "trailer", "upgrade"}

# Usuarios de prueba (Simplificacion para demostracion)
USUARIOS = {
    "admin": {"password": "admin123", "id":"1", "role": "admin"},
    "socio1": {"password": "socio123", "id":"2", "role":"socio", "socio_id": SOCIO1_SOCIO_ID},
}

client = None
bearer = HTTPBearer(auto_error=False)


@app.middleware("http")
async def correlacion(request: Request, call_next):
    recibido = request.headers.get("X-Request-Id", "")
    request.state.request_id = recibido if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", recibido) else str(uuid.uuid4())
    respuesta = await call_next(request)
    respuesta.headers["X-Request-Id"] = request.state.request_id
    return respuesta

# --- Autenticacion ---
class LoginRequest(BaseModel):
    username: str
    password: str

def limitar_tasa(request: Request):
    ip = request.client.host
    ahora = time.monotonic()
    historial = peticiones_por_ip[ip]

    # Descarte de peticiones fuera de ventana
    while historial and ahora - historial[0] > VENTANA_SEG:
        historial.popleft()

    if len(historial) >= LIMITE_PETICIONES:
        espera = int(VENTANA_SEG - (ahora - historial[0])) + 1
        raise HTTPException(status_code=429, detail="Demasiadas peticiones", headers={"Retry-After": str(espera)})

    historial.append(ahora)


@app.post("/auth/login", dependencies=[Depends(limitar_tasa)])
def login(datos: LoginRequest):
    usuario = USUARIOS.get(datos.username)
    if usuario is None or usuario["password"] != datos.password:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    ahora = int(time.time())

    payload = {
        "sub": usuario["id"],
        "role": usuario["role"],
        "iat": ahora,
        "exp": ahora +JWT_DURACION_SEG
    }
    if usuario["role"] == "socio":
        payload["socio_id"] = usuario["socio_id"]
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITMO)
    return {"access_token": token, "token_type": "bearer"}

def autenticar(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(status_code=401, detail="Token ausente")
    try:
        claims = jwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALGORITMO], options={"require": ["sub", "role", "iat", "exp"]})
        if (not isinstance(claims["sub"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}", claims["sub"])
                or not isinstance(claims["role"], str) or claims["role"] not in {"admin", "socio"}
                or any(type(claims[c]) not in {int, float} or not math.isfinite(claims[c]) for c in ("iat", "exp"))
                or claims["exp"] <= claims["iat"]):
            raise jwt.InvalidTokenError("Claims de identidad inválidos")
        if claims["role"] == "socio" and (type(claims.get("socio_id")) is not int or not 0 < claims["socio_id"] <= 9223372036854775807):
            raise jwt.InvalidTokenError("La cuenta debe tener un socio_id válido y explícito")
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except (jwt.InvalidTokenError, OverflowError):
        raise HTTPException(status_code=401, detail="Token inválido")


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}

@app.api_route("/api/v1/{recurso}", methods=METODOS, dependencies=[Depends(limitar_tasa)])
@app.api_route("/api/v1/{recurso}/{path:path}", methods=METODOS, dependencies=[Depends(limitar_tasa)])
async def proxy(recurso: str, request: Request, path: str="", usuario: dict = Depends(autenticar)):

    base = RUTAS.get(recurso)
    if base is None:
        raise HTTPException(status_code=404, detail="Recurso no encontrado")
    validar_path(path)

    destino = f"{base}/v1/{recurso}"
    if path:
        destino += f"/{path}"

    return await reenviar(destino, request, usuario)


@app.api_route("/bff/web/{path:path}", methods=METODOS, dependencies=[Depends(limitar_tasa)])
async def proxy_bff(path: str, request: Request, usuario: dict = Depends(autenticar)):
    validar_path(path)
    return await reenviar(f"{BFF_URL}/bff/web/{path}", request, usuario)


def validar_path(path):
    # Starlette ya decodificó el path. HTTPX normalizaría '..' al construir la
    # URL interna, escapando de /v1 o /bff/web con la credencial técnica inyectada.
    if (any(segmento in {".", ".."} for segmento in path.split("/"))
            or any(c in path for c in ("\\", "?", "#"))
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise HTTPException(status_code=404, detail="Recurso no encontrado")


async def reenviar(destino, request, usuario):

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in CABECERAS_IGNORADAS
        and not k.lower().startswith(("x-user-", "x-socio-"))
    }

    # Verificacion de identidad
    headers["X-User-Id"] = str(usuario["sub"])
    headers["X-User-Role"] = str(usuario["role"])
    headers["X-API-Key"] = API_KEY_INTERNA
    headers["X-Request-Id"] = request.state.request_id
    if usuario["role"] == "socio":
        headers["X-Socio-Id"] = str(usuario["socio_id"])

    cuerpo = await request.body()

    try:
        async with asyncio.timeout(UPSTREAM_TIMEOUT):
            r = await client.request(
                request.method,
                destino,
                params=request.query_params,
                headers=headers,
                content=cuerpo,
            )
    except (TimeoutError, httpx.TimeoutException):
        return JSONResponse({"codigo": "UPSTREAM_TIMEOUT", "mensaje": "Tiempo de espera del servicio agotado; consulte la misma clave si era una escritura"}, status_code=504)
    except httpx.ConnectError:
        return JSONResponse({"codigo": "UPSTREAM_NO_DISPONIBLE", "mensaje": "Servicio no disponible"}, status_code=503)
    except httpx.RequestError:
        return JSONResponse({"codigo": "UPSTREAM_RESPUESTA_INVALIDA", "mensaje": "Respuesta o comunicación remota inválida"}, status_code=502)

    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type"),
        headers={k: r.headers[k] for k in ("Retry-After", "Idempotency-Key", "WWW-Authenticate", "Allow") if k in r.headers},
    )
