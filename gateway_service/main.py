import os
import time

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from collections import defaultdict, deque

app = FastAPI(title="API Gateway Biblioteca")

# --- Configuracion ---
PRESTAMOS_URL = os.getenv("PRESTAMOS_URL", "http://prestamos:8000")
API_KEY_INTERNA = os.getenv("API_KEY", "")
JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_cambiar")
JWT_ALGORITMO = "HS256"
JWT_DURACION_SEG = 3600
LIMITE_PETICIONES = int(os.getenv("RATE_LIMIT", "20"))
VENTANA_SEG = int(os.getenv("RATE_WINDOW", "60"))

peticiones_por_ip = defaultdict(deque)

RUTAS = {"prestamos": PRESTAMOS_URL, "socios": PRESTAMOS_URL}
METODOS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
CABECERAS_IGNORADAS = {"host", "content-length", "authorization", "x-api-key", "x-user-id", "x-user-role"}

# Usuarios de prueba (Simplificacion para demostracion)
USUARIOS = {
    "admin": {"password": "admin123", "id":"1", "role": "admin"},
    "socio1": {"password": "socio123", "id":"2", "role":"socio"},
}

client = httpx.AsyncClient(timeout=5.0)
bearer = HTTPBearer(auto_error=False)

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
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITMO)
    return {"access_token": token, "token_type": "bearer"}

def autenticar(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if cred is None:
        raise HTTPException(status_code=401, detail="Token ausente")
    try:
        return jwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALGORITMO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}

@app.api_route("/api/v1/{recurso}", methods=METODOS, dependencies=[Depends(limitar_tasa)])
@app.api_route("/api/v1/{recurso}/{path:path}", methods=METODOS, dependencies=[Depends(limitar_tasa)])
async def proxy(recurso: str, request: Request, path: str="", usuario: dict = Depends(autenticar)):

    base = RUTAS.get(recurso)
    if base is None:
        return JSONResponse({"detail": "Recurso no encontrado"}, status_code=404)

    destino = f"{base}/v1/{recurso}"
    if path:
        destino += f"/{path}"

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in CABECERAS_IGNORADAS
    }

    # Verificacion de identidad
    headers["X-User-Id"] = str(usuario["sub"])
    headers["X-User-Role"] = str(usuario["role"])
    headers["X-API-Key"] = API_KEY_INTERNA

    cuerpo = await request.body()

    try:
        r = await client.request(
            request.method,
            destino,
            params=request.query_params,
            headers=headers,
            content=cuerpo,
        )
    except httpx.RequestError:
        return JSONResponse({"detail": "Servicio no disponible"}, status_code=502)

    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type")
    )