from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from typing import List, Optional

from src.database import get_db
from src.schemas import SocioInput, SocioOut, ErrorResponse
from src.auth import verificar_api_key
from src import models

router = APIRouter(
    prefix="/v1/socios",
    tags=["Socios"],
    dependencies=[Depends(verificar_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Error en la solicitud"},
        404: {"model": ErrorResponse, "description": "Socio no encontrado"},
        409: {"model": ErrorResponse, "description": "RUT o correo duplicado"},
    }
)

@router.get("", response_model=List[SocioOut])
def listar_socios(rut: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(models.Socio)
    if rut is not None:
        query = query.filter(models.Socio.rut == rut)
    return query.all()

@router.post("", response_model=SocioOut, status_code=201)
def crear_socio(socio: SocioInput, db: Session = Depends(get_db)):
    # Validación de RUT duplicado
    existente_rut = db.query(models.Socio).filter(models.Socio.rut == socio.rut).first()
    if existente_rut is not None:
        raise HTTPException(
            status_code=409,
            detail={"codigo": "RUT_DUPLICADO", "mensaje": "Ya existe un socio con ese RUT"}
        )

    # Validación de correo duplicado
    existente = db.query(models.Socio).filter(models.Socio.correo == socio.correo).first()
    if existente is not None:
        raise HTTPException(
            status_code=409,
            detail={"codigo": "CORREO_DUPLICADO", "mensaje": "Ya existe un socio con ese correo"}
        )

    # Creación del nuevo socio
    nuevo_socio = models.Socio(rut=socio.rut, nombre=socio.nombre, correo=socio.correo)
    db.add(nuevo_socio)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(models.Socio).filter_by(rut=socio.rut).first():
            codigo, mensaje = "RUT_DUPLICADO", "Ya existe un socio con ese RUT"
        elif db.query(models.Socio).filter_by(correo=socio.correo).first():
            codigo, mensaje = "CORREO_DUPLICADO", "Ya existe un socio con ese correo"
        else:
            raise
        raise HTTPException(status_code=409, detail={"codigo": codigo, "mensaje": mensaje})
    db.refresh(nuevo_socio)
    return nuevo_socio

@router.get("/{socio_id}", response_model=SocioOut)
def consultar_socio(socio_id: int, db: Session = Depends(get_db)):
    socio = db.get(models.Socio, socio_id)
    if socio is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SOCIO_NO_ENCONTRADO", "mensaje": "No existe un Socio con ese ID"}
        )

    return socio
