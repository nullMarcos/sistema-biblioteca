import os

from fastapi import Header, HTTPException

API_KEY_VALIDA = os.getenv("API_KEY")

def verificar_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY_VALIDA:
        raise HTTPException(status_code=401, detail="API KEY inválida o ausente")