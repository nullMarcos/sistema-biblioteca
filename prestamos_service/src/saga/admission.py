"""Admisión serializada en SQLite: identidad, duplicados y reserva de cupo."""

import json
import os
import uuid
import time

from sqlalchemy import or_
from src import models, schemas
from src.database import escritura
from src.saga.exceptions import ReglaPrestamoError, SagaStepError
from src.saga.storage import LEASE_SECONDS

MAX_PRESTAMOS_POR_SOCIO = int(os.getenv("MAX_PRESTAMOS_POR_SOCIO", "5"))
if MAX_PRESTAMOS_POR_SOCIO <= 0:
    raise RuntimeError("MAX_PRESTAMOS_POR_SOCIO debe ser positivo")
OCUPAN_CUPO = ("ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE")


def admitir(db, payload, actor_id=None, owner=None):
    if actor_id is not None and (not actor_id or len(actor_id) > 128):
        raise ReglaPrestamoError("Identidad interna inválida", "ACTOR_INVALIDO", 400)
    normalizado = json.dumps({"socio_id": payload.socio_id, "libro_id": payload.libro_id,
        "simular_fallo_paso3": bool(payload.simular_fallo_paso3), "actor_id": actor_id}, sort_keys=True)
    clave = payload.idempotency_key or str(uuid.uuid4())
    with escritura(db):
        previa = db.query(models.SagaTransaccion).filter_by(idempotency_key=clave).first()
        if previa:
            if (previa.socio_id != payload.socio_id or previa.libro_id != payload.libro_id
                    or previa.actor_id != actor_id or (previa.payload_json and previa.payload_json != normalizado)):
                raise ReglaPrestamoError("La clave ya pertenece a otra solicitud o actor", "IDEMPOTENCIA_PAYLOAD_DISTINTO")
            return previa, False
        if db.get(models.Socio, payload.socio_id) is None:
            raise SagaStepError("INICIO", "No existe un socio con ese ID", "SOCIO_NO_ENCONTRADO")
        prestamos = db.query(models.Prestamo).filter(models.Prestamo.socio_id == payload.socio_id,
            models.Prestamo.estado.in_(OCUPAN_CUPO))
        # La Saga entrega su cupo al préstamo en la misma confirmación local.
        pendientes = db.query(models.SagaTransaccion).filter_by(socio_id=payload.socio_id, cupo_reservado=True).filter(
            or_(models.SagaTransaccion.prestamo_id.is_(None), ~models.SagaTransaccion.prestamo_id.in_(
                db.query(models.Prestamo.id).filter(models.Prestamo.estado.in_(OCUPAN_CUPO)))))
        if prestamos.filter_by(libro_id=payload.libro_id).first() or pendientes.filter_by(libro_id=payload.libro_id).first():
            raise ReglaPrestamoError("Ya existe un préstamo o solicitud pendiente para este socio/libro", "PRESTAMO_DUPLICADO")
        if prestamos.count() + pendientes.count() >= MAX_PRESTAMOS_POR_SOCIO:
            raise ReglaPrestamoError("Se alcanzó el máximo configurado de préstamos", "CUPO_PRESTAMOS_AGOTADO")
        saga_id = str(uuid.uuid4())
        registro = models.SagaTransaccion(saga_id=saga_id, idempotency_key=clave,
            socio_id=payload.socio_id, libro_id=payload.libro_id, estado="INICIADA", paso_actual="INICIO",
            payload_json=normalizado, actor_id=actor_id, cupo_reservado=True,
            reserva_id=saga_id, compensacion_id=saga_id + ":compensar", accion_pendiente="RESERVAR_EJEMPLAR",
            lease_owner=owner, lease_until=time.time() + LEASE_SECONDS)
        db.add(registro)
    return registro, True


def resultado_previo(registro):
    if registro.estado == "COMPLETADA" and registro.respuesta_json:
        return schemas.PrestamoOut.model_validate_json(registro.respuesta_json)
    if registro.estado in {"COMPENSADA", "FALLIDA"}:
        guardado = json.loads(registro.respuesta_json or "{}")
        if "error" in guardado:
            raise ReglaPrestamoError(guardado["error"]["mensaje"], guardado["error"]["codigo"], guardado["http"])
        raise ReglaPrestamoError("La operación ya terminó y no se volverá a ejecutar", "OPERACION_TERMINADA")
    raise ReglaPrestamoError(f"Operación pendiente ({registro.saga_id}); consulte su estado y reintente con la misma clave", "OPERACION_PENDIENTE")
