from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from typing import List, Optional
from datetime import date, datetime, timedelta, timezone

from src.database import get_db
from src.schemas import PrestamoInput, PrestamoOut
from src.auth import verificar_api_key
from src import models
from src.grpc_client import reservar_ejemplar, liberar_ejemplar, LiberacionFallidaError, SinStockError, CatalogoNoDisponibleError

router = APIRouter(prefix="/v1/prestamos", tags=["Prestamos"], dependencies=[Depends(verificar_api_key)])

@router.get("", response_model=List[PrestamoOut])
def listar_prestamos(estado: Optional[str] = None, db: Session = Depends(get_db)):
    """Se filtra por el parametro [estado], si es None se listan todos lo prestamos"""
    query = db.query(models.Prestamo)
    if estado is not None:
        query = query.filter(models.Prestamo.estado == estado)

    return query.all()

@router.post("", response_model=PrestamoOut, status_code=201)
def crear_prestamo(prestamo: PrestamoInput, db: Session = Depends(get_db)):
    # Validacion de socio existente
    socio_existente = db.get(models.Socio, prestamo.socio_id)
    if socio_existente is None:
        raise HTTPException(status_code=404, detail="No existe un Socio con ese ID")

    # Intento de reservar el ejemplar
    try:
        ejemplar_id = reservar_ejemplar(prestamo.libro_id)
    except SinStockError:
        raise HTTPException(status_code=409, detail="No hay stock disponible")
    except CatalogoNoDisponibleError:
        raise HTTPException(status_code=503, detail="El servicio de Catálogo no responde. La operación no se completó")

    # Creacion y guardado del prestamo
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
    # Validar existencia del prestamo
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(status_code=404, detail="No existe un Prestamo con ese ID") # No deberia ser 409 si ese el error de No_Encontrado?? igual en el get de socio {socio_id}

    return prestamo

@router.delete("/{prestamo_id}", response_model=PrestamoOut)
def eliminar_prestamo(prestamo_id: int, db: Session = Depends(get_db)):
    # Validar existencia del prestamo
    prestamo = db.get(models.Prestamo, prestamo_id)
    if prestamo is None:
        raise HTTPException(status_code=404, detail="No existe un Prestamo con ese ID")

    # Validacion de prestamos ya devuelto
    estado = prestamo.estado
    if estado == "DEVUELTO":
        raise HTTPException(status_code=409, detail="El préstamo ya fue devuelto anteriormente")

    # Bloque try/except para atrapar caida del sistema de catalogo
    try:
        liberar_ejemplar(prestamo.ejemplar_id)
    except LiberacionFallidaError:
        raise HTTPException(status_code=409, detail="No se pudo liberar el ejemplar en Catalogo")
    except CatalogoNoDisponibleError:
        raise HTTPException(status_code=503, detail="El servicio de Catálogo no responde. La operación no se completó")

    # Actualizacion del pretamo
    prestamo.estado = "DEVUELTO"
    prestamo.fecha_devolucion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(prestamo)

    return prestamo
    