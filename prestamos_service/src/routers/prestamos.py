from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from typing import List, Optional
from datetime import date, datetime, timedelta, timezone

from src.database import get_db
from src.schemas import PrestamoInput, PrestamoOut, SagaStatusOut, ErrorResponse
from src.auth import verificar_api_key
from src import models
from src.grpc_client import (
    reservar_ejemplar,
    liberar_ejemplar,
    LiberacionFallidaError,
    SinStockError,
    CatalogoNoDisponibleError,
    CatalogoTimeoutError,
    CatalogoNotFoundError,
    CatalogoInvalidArgumentError,
    CatalogoError,
    CircuitBreakerOpenError,
)
from src.saga import (
    PrestamoSagaOrchestrator,
    SagaStepError,
    SocioSancionadoError,
    IdempotencyConflictError,
    SagaCompensationError,
)

router = APIRouter(
    prefix="/v1/prestamos",
    tags=["Prestamos"],
    dependencies=[Depends(verificar_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Error en los parámetros de la solicitud"},
        404: {"model": ErrorResponse, "description": "Recurso no encontrado"},
        409: {"model": ErrorResponse, "description": "Conflicto en la operación"},
        503: {"model": ErrorResponse, "description": "Servicio de Catálogo no disponible"},
    }
)

def _inyectar_hateoas(prestamo: models.Prestamo) -> PrestamoOut:
    p_out = PrestamoOut.model_validate(prestamo)
    links = {
        "self": {"href": f"/v1/prestamos/{p_out.id}", "method": "GET"},
        "socio": {"href": f"/v1/socios/{p_out.socio_id}", "method": "GET"}
    }
    if p_out.estado == "ACTIVO":
        links["devolver"] = {"href": f"/v1/prestamos/{p_out.id}", "method": "DELETE"}
    p_out.links = links
    return p_out

@router.get("", response_model=List[PrestamoOut])
def listar_prestamos(estado: Optional[str] = None, db: Session = Depends(get_db)):
    """Se filtra por el parámetro [estado], si es None se listan todos los préstamos."""
    query = db.query(models.Prestamo)
    if estado is not None:
        query = query.filter(models.Prestamo.estado == estado)

    return [_inyectar_hateoas(p) for p in query.all()]

@router.post("", response_model=PrestamoOut, status_code=201)
def crear_prestamo(prestamo: PrestamoInput, db: Session = Depends(get_db)):
    """
    Registra el préstamo de un libro ejecutando la transacción distribuida con el patrón Saga.
    Coordina Reserva en Catálogo, Verificación en Sanciones y Persistencia en Préstamos.
    """
    orquestador = PrestamoSagaOrchestrator(db=db)
    try:
        nuevo_prestamo, ctx = orquestador.ejecutar(prestamo)
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
    except CatalogoError as e:
        raise HTTPException(status_code=502, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except SagaStepError as e:
        status_code = 404 if e.codigo == "SOCIO_NO_ENCONTRADO" else 500
        raise HTTPException(status_code=status_code, detail={"codigo": e.codigo, "mensaje": e.mensaje})
    except SagaCompensationError as e:
        raise HTTPException(status_code=500, detail={"codigo": e.codigo, "mensaje": e.mensaje})

@router.get("/saga/{saga_id}", response_model=SagaStatusOut)
def consultar_estado_saga(saga_id: str, db: Session = Depends(get_db)):
    """Consulta el estado auditado de una transacción Saga por su UUID."""
    registro = db.query(models.SagaTransaccion).filter(models.SagaTransaccion.saga_id == saga_id).first()
    if not registro:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SAGA_NO_ENCONTRADA", "mensaje": f"No se encontró registro para la saga '{saga_id}'"}
        )
    return registro

@router.get("/saga/idempotency/{idempotency_key}", response_model=SagaStatusOut)
def consultar_saga_por_idempotencia(idempotency_key: str, db: Session = Depends(get_db)):
    """Consulta el estado de una transacción Saga por su clave de idempotencia."""
    registro = db.query(models.SagaTransaccion).filter(models.SagaTransaccion.idempotency_key == idempotency_key).first()
    if not registro:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "IDEMPOTENCIA_NO_ENCONTRADA", "mensaje": f"No se encontró registro para la clave '{idempotency_key}'"}
        )
    return registro

@router.get("/{prestamo_id}", response_model=PrestamoOut)
def consultar_prestamo(prestamo_id: int, db: Session = Depends(get_db)):
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe un Prestamo con ese ID"}
        )

    return _inyectar_hateoas(prestamo)

@router.delete("/{prestamo_id}", response_model=PrestamoOut)
def eliminar_prestamo(prestamo_id: int, db: Session = Depends(get_db)):
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe un Prestamo con ese ID"}
        )

    # Validación de préstamo ya devuelto
    if prestamo.estado == "DEVUELTO":
        raise HTTPException(
            status_code=409,
            detail={"codigo": "PRESTAMO_YA_DEVUELTO", "mensaje": "El préstamo ya fue devuelto anteriormente"}
        )

    # Liberación del ejemplar en Catálogo con manejo granular de errores
    try:
        liberar_ejemplar(prestamo.ejemplar_id)
    except LiberacionFallidaError as e:
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
    except CatalogoError as e:
        raise HTTPException(status_code=502, detail={"codigo": e.codigo, "mensaje": e.mensaje})

    # Actualización del estado del préstamo
    prestamo.estado = "DEVUELTO"
    prestamo.fecha_devolucion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(prestamo)

    return _inyectar_hateoas(prestamo)