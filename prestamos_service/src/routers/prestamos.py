from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from typing import List, Optional
from datetime import date, datetime, timedelta, timezone

from src.database import get_db
from src.schemas import PrestamoInput, PrestamoOut, ErrorResponse
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

@router.get("", response_model=List[PrestamoOut])
def listar_prestamos(estado: Optional[str] = None, db: Session = Depends(get_db)):
    """Se filtra por el parámetro [estado], si es None se listan todos los préstamos."""
    query = db.query(models.Prestamo)
    if estado is not None:
        query = query.filter(models.Prestamo.estado == estado)

    return query.all()

@router.post("", response_model=PrestamoOut, status_code=201)
def crear_prestamo(prestamo: PrestamoInput, db: Session = Depends(get_db)):
    # Validación de socio existente
    socio_existente = db.get(models.Socio, prestamo.socio_id)
    if socio_existente is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SOCIO_NO_ENCONTRADO", "mensaje": "No existe un Socio con ese ID"}
        )

    # Intento de reservar el ejemplar en Catálogo con manejo granular de errores
    try:
        ejemplar_id = reservar_ejemplar(prestamo.libro_id)
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

    # Creación y guardado del préstamo
    nuevo_prestamo = models.Prestamo(
        socio_id=prestamo.socio_id,
        libro_id=prestamo.libro_id,
        ejemplar_id=ejemplar_id,
        fecha_limite=date.today() + timedelta(days=14)
    )
    db.add(nuevo_prestamo)
    db.commit()
    db.refresh(nuevo_prestamo)

    return nuevo_prestamo

@router.get("/{prestamo_id}", response_model=PrestamoOut)
def consultar_prestamo(prestamo_id: int, db: Session = Depends(get_db)):
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "PRESTAMO_NO_ENCONTRADO", "mensaje": "No existe un Prestamo con ese ID"}
        )

    return prestamo

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

    return prestamo