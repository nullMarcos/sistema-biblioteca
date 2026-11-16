"""Transacciones locales y propiedad persistente, sin locks de Python."""

from contextlib import contextmanager
import os
import time

from src.database import escritura
from src.saga.exceptions import LeasePerdidoError

LEASE_SECONDS = float(os.getenv("SAGA_LEASE_SECONDS", "30"))
MAX_INTENTOS = int(os.getenv("SAGA_RECUPERACION_MAX_INTENTOS", "5"))
BACKOFF_INICIAL = float(os.getenv("SAGA_RECUPERACION_BACKOFF", "1"))
BACKOFF_MAXIMO = float(os.getenv("SAGA_RECUPERACION_BACKOFF_MAX", "60"))
if LEASE_SECONDS <= 0:
    raise RuntimeError("SAGA_LEASE_SECONDS debe ser positivo")
if MAX_INTENTOS <= 0 or not 0 < BACKOFF_INICIAL <= BACKOFF_MAXIMO:
    raise RuntimeError("Intentos y backoff de recuperación inválidos")
TERMINALES = ("COMPLETADA", "COMPENSADA", "FALLIDA")


def proximo_intento(intentos):
    return time.time() + min(BACKOFF_MAXIMO, BACKOFF_INICIAL * 2 ** min(intentos, 20))


def validar_presupuesto_lease(grpc_client, sanciones_client):
    timeout = getattr(grpc_client, "TIME_OUT_SEGS", 0)
    retries = getattr(grpc_client, "MAX_RETRIES", 0)
    backoff = getattr(grpc_client, "INITIAL_BACKOFF_SEGS", 0)
    red = max(timeout * (retries + 1) + backoff * (2 ** retries - 1), getattr(sanciones_client, "timeout", 0))
    minimo = red + 2 * float(os.getenv("SQLITE_BUSY_TIMEOUT", "0.5")) + 1
    if LEASE_SECONDS <= minimo:
        raise RuntimeError(f"SAGA_LEASE_SECONDS debe superar {minimo:.2f}s para el presupuesto remoto configurado")


def soltar(registro):
    registro.lease_owner = None
    registro.lease_until = 0


@contextmanager
def escritura_propietaria(db, modelo, identificador, owner):
    with escritura(db):
        registro = db.get(modelo, identificador, populate_existing=True)
        if registro is None or registro.lease_owner != owner or registro.lease_until <= time.time():
            correlacion = getattr(registro, "saga_id", None) or getattr(registro, "operacion_id", identificador)
            raise LeasePerdidoError(correlacion, "La ejecución perdió su lease; no puede confirmar ni compensar")
        registro.lease_until = time.time() + LEASE_SECONDS
        yield registro
