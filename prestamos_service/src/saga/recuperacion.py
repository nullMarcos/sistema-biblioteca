"""Runner interno acotado. Invocar dentro de Préstamos, nunca desde el BFF."""

import argparse
import json
import logging
import time

from src import models
from src.database import SessionLocal, escritura, migrar_esquema, Base, engine
from src.saga.orchestrator import PrestamoSagaOrchestrator
from src.saga.devolucion import DevolucionService
from src.saga.storage import LEASE_SECONDS, MAX_INTENTOS, TERMINALES
from src.saga.exceptions import SagaError

logger = logging.getLogger(__name__)


def recuperar(factory=SessionLocal, limite=10, grpc_client=None, reanudar=False, saga_id=None, devolucion_id=None):
    if not 1 <= limite <= 100:
        raise ValueError("El límite debe estar entre 1 y 100")
    if (saga_id and devolucion_id) or (reanudar and not (saga_id or devolucion_id)):
        raise ValueError("La reanudación requiere una única saga_id o devolucion_id concreta")
    resultado = {"procesadas": 0, "resueltas": 0, "pendientes": 0, "operaciones": []}
    resultado["en_pausa"] = 0
    tipos = [
        (models.SagaTransaccion, "saga_id", saga_id, TERMINALES, PrestamoSagaOrchestrator),
        (models.DevolucionOperacion, "operacion_id", devolucion_id, ("COMPLETADA",), DevolucionService),
    ]
    for modelo, campo, filtro, terminales, constructor in tipos:
        if (saga_id or devolucion_id) and not filtro:
            continue

        def consulta(db):
            query = db.query(modelo).filter(modelo.estado.not_in(terminales))
            return query.filter(getattr(modelo, campo) == filtro) if filtro else query

        with factory() as db:
            with escritura(db):
                query = consulta(db)
                if reanudar:
                    for registro in query.filter(modelo.lease_until <= time.time()).all():
                        registro.recuperacion_intentos = 0
                        registro.proximo_intento = 0
                db.flush()
                ids = [r.id for r in query.filter(modelo.lease_until <= time.time(), modelo.proximo_intento <= time.time(),
                    modelo.recuperacion_intentos < MAX_INTENTOS).order_by(modelo.id).limit(limite - resultado["procesadas"])]
        for identificador in ids:
            with factory() as db:
                worker = constructor(db, grpc_client=grpc_client)
                with escritura(db):
                    registro = db.get(modelo, identificador)
                    ahora = time.time()
                    if (registro.estado in terminales or registro.lease_until > ahora
                            or registro.proximo_intento > ahora or registro.recuperacion_intentos >= MAX_INTENTOS):
                        continue
                    registro.lease_owner = worker.owner
                    registro.lease_until = ahora + LEASE_SECONDS
                    registro.recuperacion_intentos += 1
                resultado["procesadas"] += 1
                correlacion = getattr(registro, campo)
                try:
                    if modelo is models.SagaTransaccion:
                        # Cancelar el flujo interrumpido; no aprobar con una verificación antigua.
                        worker.compensar(worker.contexto(registro), registro, motivo=registro.error_detalle or "Proceso interrumpido")
                    else:
                        worker.ejecutar_pendiente(identificador)
                    resultado["resueltas"] += 1
                except SagaError:
                    logger.warning("operacion=%s recuperación todavía pendiente", correlacion)
                db.rollback()
                actual = db.get(modelo, identificador)
                resultado["operaciones"].append({campo: correlacion, "tipo": modelo.__tablename__, "estado": actual.estado,
                    "accion_pendiente": actual.accion_pendiente, "intentos": actual.recuperacion_intentos})
        with factory() as db:
            query = consulta(db)
            resultado["pendientes"] += query.count()
            resultado["en_pausa"] += query.filter(modelo.recuperacion_intentos >= MAX_INTENTOS).count()
    return resultado


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=10)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--saga-id")
    selector.add_argument("--devolucion-id")
    parser.add_argument("--reanudar", action="store_true", help="Reiniciar intentos/backoff solo para la operación indicada y sin lease activo")
    args = parser.parse_args()
    if not 1 <= args.limite <= 100 or (args.reanudar and not (args.saga_id or args.devolucion_id)):
        parser.error("Use --limite entre 1 y 100; --reanudar requiere --saga-id o --devolucion-id")
    logging.basicConfig(level=logging.INFO)
    try:
        migrar_esquema()
        Base.metadata.create_all(bind=engine)
        resultado = recuperar(limite=args.limite, reanudar=args.reanudar, saga_id=args.saga_id, devolucion_id=args.devolucion_id)
        print(json.dumps(resultado))
        return 2 if resultado["pendientes"] else 0
    finally:
        from src import grpc_client
        grpc_client.cerrar_canal()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
