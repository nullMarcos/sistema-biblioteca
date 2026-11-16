"""Presupuesto monotónico compartido por una operación; no crea workers ni retries."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import math
import os
import time

CATALOGO_TIMEOUT = float(os.getenv("CATALOGO_TIMEOUT_SEGS", "0.6"))
CATALOGO_RETRIES = int(os.getenv("CATALOGO_MAX_RETRIES", "1"))
CATALOGO_BACKOFF = float(os.getenv("CATALOGO_INITIAL_BACKOFF_SEGS", "0.1"))
SANCIONES_TIMEOUT = float(os.getenv("SANCIONES_TIMEOUT_SEGS", "1.5"))
SQLITE_ESPERA = float(os.getenv("SQLITE_BUSY_TIMEOUT", "0.5"))
SAGA_TIMEOUT = float(os.getenv("SAGA_TIMEOUT_SEGS", "8"))
if (not 0 <= CATALOGO_RETRIES <= 5 or not all(math.isfinite(v) and v > 0 for v in
        (CATALOGO_TIMEOUT, SANCIONES_TIMEOUT, SQLITE_ESPERA, SAGA_TIMEOUT))
        or not math.isfinite(CATALOGO_BACKOFF) or CATALOGO_BACKOFF < 0):
    raise RuntimeError("Configuración de tiempos/reintentos inválida")
CATALOGO_PRESUPUESTO = CATALOGO_TIMEOUT * (CATALOGO_RETRIES + 1) + CATALOGO_BACKOFF * (2 ** CATALOGO_RETRIES - 1)
if SAGA_TIMEOUT <= 2 * CATALOGO_PRESUPUESTO + SANCIONES_TIMEOUT + 2 * SQLITE_ESPERA:
    raise RuntimeError("SAGA_TIMEOUT_SEGS debe cubrir reserva, compensación, Sanciones y margen local SQLite")

_deadline = ContextVar("deadline_operacion", default=None)


class PresupuestoAgotado(TimeoutError):
    codigo = "OPERACION_TIMEOUT"
    mensaje = "Se agotó el presupuesto de la operación; consulte/reutilice su identidad"
    http_status = 504


def restante(maximo=None):
    limite = _deadline.get()
    disponible = maximo if limite is None else limite - time.monotonic()
    if disponible is not None and disponible <= 0:
        raise PresupuestoAgotado(PresupuestoAgotado.mensaje)
    return min(maximo, disponible) if maximo is not None and disponible is not None else disponible


@contextmanager
def presupuesto(segundos=None):
    previo = _deadline.get()
    limite = time.monotonic() + (SAGA_TIMEOUT if segundos is None else segundos)
    token = _deadline.set(min(previo, limite) if previo is not None else limite)
    try:
        yield
    finally:
        _deadline.reset(token)


def limitar_operacion(func):
    @wraps(func)
    def limitada(*args, **kwargs):
        with presupuesto():
            return func(*args, **kwargs)
    return limitada


def configuracion():
    return {"catalogo_timeout_s": CATALOGO_TIMEOUT, "catalogo_reintentos": CATALOGO_RETRIES,
        "catalogo_backoff_inicial_s": CATALOGO_BACKOFF, "catalogo_presupuesto_rpc_s": CATALOGO_PRESUPUESTO,
        "sanciones_timeout_total_s": SANCIONES_TIMEOUT, "sqlite_espera_escritor_s": SQLITE_ESPERA,
        "saga_timeout_s": SAGA_TIMEOUT,
        "margen_local_s": SAGA_TIMEOUT - 2 * CATALOGO_PRESUPUESTO - SANCIONES_TIMEOUT}
