import os

from fastapi import APIRouter, Depends, HTTPException, Request, Header, Path
from sqlalchemy.orm import Session

from typing import List, Optional, Annotated

from src.database import get_db
from src.schemas import PrestamoInput, PrestamoOut, SagaStatusOut, DevolucionStatusOut, ClaveIdempotencia, ErrorResponse
from src.auth import verificar_api_key
from src.identity import Identidad, identidad_interna
from src import models
from src.grpc_client import (
    SinStockError,
    CatalogoNoDisponibleError,
    CatalogoTimeoutError,
    CatalogoNotFoundError,
    CatalogoInvalidArgumentError,
    CatalogoError,
    CatalogoConflictoError,
    CircuitBreakerOpenError,
)
from src.saga import (
    PrestamoSagaOrchestrator,
    SagaStepError,
    SocioSancionadoError,
    IdempotencyConflictError,
    SagaCompensationError,
)
from src.saga.devolucion import DevolucionService

router = APIRouter(
    prefix="/v1/prestamos",
    tags=["Prestamos"],
    dependencies=[Depends(verificar_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Error en los parámetros de la solicitud"},
        401: {"model": ErrorResponse, "description": "API Key ausente o inválida"},
        403: {"model": ErrorResponse, "description": "Identidad sin acceso al socio o a la operación"},
        404: {"model": ErrorResponse, "description": "Recurso no encontrado"},
        409: {"model": ErrorResponse, "description": "Conflicto en la operación"},
        500: {"model": ErrorResponse, "description": "Error real de persistencia o ejecución"},
        502: {"model": ErrorResponse, "description": "Respuesta remota inválida o error de protocolo"},
        503: {"model": ErrorResponse, "description": "Indisponibilidad, circuito abierto o recuperación pendiente"},
        504: {"model": ErrorResponse, "description": "Tiempo de espera remoto agotado"},
    }
)

def _inyectar_hateoas(prestamo: models.Prestamo) -> PrestamoOut:
    p_out = PrestamoOut.model_validate(prestamo)
    links = {
        "self": {"href": f"/v1/prestamos/{p_out.id}", "method": "GET"},
        "socio": {"href": f"/v1/socios/{p_out.socio_id}", "method": "GET"}
    }
    if p_out.estado == "ACTIVO":
        links["devolver"] = {"href": f"/v1/prestamos/{p_out.id}/devolucion", "method": "POST"}
    elif p_out.estado == "EN_REVISION":
        links["cancelar"] = {"href": f"/v1/prestamos/{p_out.id}/devolucion", "method": "POST"}
    p_out.links = links
    return p_out

@router.get("", response_model=List[PrestamoOut])
def listar_prestamos(estado: Optional[str] = None, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    """Se filtra por el parámetro [estado], si es None se listan todos los préstamos."""
    query = db.query(models.Prestamo)
    if identidad.rol == "socio":
        query = query.filter(models.Prestamo.socio_id == identidad.socio_id)
    if estado is not None:
        query = query.filter(models.Prestamo.estado == estado)

    return [_inyectar_hateoas(p) for p in query.all()]

@router.post("", response_model=PrestamoOut, status_code=201,
    responses={403: {"model": ErrorResponse, "description": "Socio ajeno o inyección de fallo no habilitada/autorizada"}})
def crear_prestamo(prestamo: PrestamoInput, request: Request, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    """
    Registra el préstamo de un libro ejecutando la transacción distribuida con el patrón Saga.
    Coordina Reserva en Catálogo, Verificación en Sanciones y Persistencia en Préstamos.
    """
    identidad.autorizar(prestamo.socio_id)
    if prestamo.simular_fallo_paso3:
        if os.getenv("HABILITAR_FALLOS_DEMO", "false").lower() != "true":
            raise HTTPException(status_code=403, detail={"codigo": "DEMO_DESHABILITADA", "mensaje": "La inyección de fallos no está habilitada"})
        if identidad.rol != "admin" or not identidad.cuenta:
            raise HTTPException(status_code=403, detail={"codigo": "DEMO_NO_AUTORIZADA", "mensaje": "Se requiere un actor administrador autenticado"})
    orquestador = PrestamoSagaOrchestrator(db=db)
    try:
        nuevo_prestamo, ctx = orquestador.ejecutar(prestamo, actor_id=identidad.cuenta)
        p_out = _inyectar_hateoas(nuevo_prestamo)
        p_out.idempotency_key = ctx.idempotency_key
        return p_out
    except SocioSancionadoError as e:
        raise HTTPException(status_code=409, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except IdempotencyConflictError as e:
        raise HTTPException(status_code=409, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except SinStockError as e:
        raise HTTPException(status_code=409, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoNotFoundError as e:
        raise HTTPException(status_code=404, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoInvalidArgumentError as e:
        raise HTTPException(status_code=400, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CircuitBreakerOpenError as e:
        raise HTTPException(status_code=503, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoTimeoutError as e:
        raise HTTPException(status_code=504, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoNoDisponibleError as e:
        raise HTTPException(status_code=503, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoConflictoError as e:
        raise HTTPException(status_code=409, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except CatalogoError as e:
        raise HTTPException(status_code=502, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except SagaStepError as e:
        status_code = 404 if e.codigo == "SOCIO_NO_ENCONTRADO" else 500
        raise HTTPException(status_code=status_code, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except SagaCompensationError as e:
        raise HTTPException(status_code=500, detail={"codigo": e.codigo, "mensaje": e.mensaje})

@router.get("/saga/{saga_id}", response_model=SagaStatusOut)
def consultar_estado_saga(saga_id: str, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    """Consulta el estado auditado de una transacción Saga por su UUID."""
    registro = db.query(models.SagaTransaccion).filter(models.SagaTransaccion.saga_id == saga_id).first()
    if not registro:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SAGA_NO_ENCONTRADA", "mensaje": f"No se encontró registro para la saga '{saga_id}'"}
        )
    identidad.autorizar(registro.socio_id)
    return registro

@router.get("/saga/idempotency/{idempotency_key}", response_model=SagaStatusOut)
def consultar_saga_por_idempotencia(idempotency_key: ClaveIdempotencia, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    """Consulta el estado de una transacción Saga por su clave de idempotencia."""
    registro = db.query(models.SagaTransaccion).filter(models.SagaTransaccion.idempotency_key == idempotency_key).first()
    if not registro:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "IDEMPOTENCIA_NO_ENCONTRADA", "mensaje": f"No se encontró registro para la clave '{idempotency_key}'"}
        )
    identidad.autorizar(registro.socio_id)
    return registro

@router.get("/{prestamo_id}", response_model=PrestamoOut)
def consultar_prestamo(prestamo_id: Annotated[int, Path(gt=0)], db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe un Prestamo con ese ID"}
        )

    identidad.autorizar(prestamo.socio_id)
    return _inyectar_hateoas(prestamo)

@router.get("/devoluciones/idempotency/{idempotency_key}", response_model=DevolucionStatusOut)
def consultar_devolucion(idempotency_key: ClaveIdempotencia, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    operacion = db.query(models.DevolucionOperacion).filter_by(idempotency_key=idempotency_key).first()
    if operacion is None:
        raise HTTPException(status_code=404, detail={"codigo": "DEVOLUCION_NO_ENCONTRADA", "mensaje": "No existe esa devolución"})
    if identidad.rol == "socio":
        prestamo = db.get(models.Prestamo, operacion.prestamo_id)
        if prestamo is None:
            raise HTTPException(404, {"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe ese préstamo"})
        identidad.autorizar(prestamo.socio_id)
    return operacion


@router.post("/{prestamo_id}/devolucion", response_model=PrestamoOut)
@router.delete("/{prestamo_id}", response_model=PrestamoOut, deprecated=True)
def eliminar_prestamo(prestamo_id: Annotated[int, Path(gt=0)], request: Request,
        idempotency_key: Optional[ClaveIdempotencia] = Header(None, alias="Idempotency-Key"),
        db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    """Devuelve ACTIVO; cancela EN_REVISION a ANULADO sin aprobar ni entregar el ejemplar."""
    if identidad.rol == "socio":
        prestamo = db.get(models.Prestamo, prestamo_id)
        if prestamo is None:
            raise HTTPException(404, {"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe ese préstamo"})
        identidad.autorizar(prestamo.socio_id)
    resultado = DevolucionService(db).devolver(prestamo_id, idempotency_key, actor_id=identidad.cuenta)
    return _inyectar_hateoas(resultado)
