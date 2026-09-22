import os

from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader

API_KEY_VALIDA = os.getenv("API_KEY")
if not API_KEY_VALIDA:
    raise RuntimeError("La variable de entorno API_KEY no está definida.")

api_key_header = APIKeyHeader(name="X-API-Key")

def verificar_api_key(x_api_key: str = Security(api_key_header)):
    if x_api_key != API_KEY_VALIDA:
        raise HTTPException(status_code=401, detail="API KEY inválida o ausente")