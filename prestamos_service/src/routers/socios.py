from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from typing import List, Optional, Annotated

from src.database import get_db
from src.schemas import SocioInput, SocioOut, ErrorResponse
from src.auth import verificar_api_key
from src.identity import Identidad, identidad_interna
from src import models

router = APIRouter(
    prefix="/v1/socios",
    tags=["Socios"],
    dependencies=[Depends(verificar_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Error en la solicitud"},
        401: {"model": ErrorResponse, "description": "API Key ausente o inválida"},
        403: {"model": ErrorResponse, "description": "Identidad sin acceso al recurso o a la operación"},
        404: {"model": ErrorResponse, "description": "Socio no encontrado"},
        409: {"model": ErrorResponse, "description": "RUT o correo duplicado"},
        500: {"model": ErrorResponse, "description": "Error de persistencia local"},
        503: {"model": ErrorResponse, "description": "Base de datos temporalmente ocupada"},
    }
)

@router.get("", response_model=List[SocioOut])
def listar_socios(rut: Optional[str] = None, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    query = db.query(models.Socio)
    if identidad.rol == "socio":
        query = query.filter(models.Socio.id == identidad.socio_id)
    if rut is not None:
        query = query.filter(models.Socio.rut == rut)
    return query.all()

@router.post("", response_model=SocioOut, status_code=201)
def crear_socio(socio: SocioInput, db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    identidad.exigir_personal()
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
def consultar_socio(socio_id: Annotated[int, Path(gt=0, lt=9223372036854775808)], db: Session = Depends(get_db), identidad: Identidad = Depends(identidad_interna)):
    identidad.autorizar(socio_id)
    socio = db.get(models.Socio, socio_id)
    if socio is None:
        raise HTTPException(
            status_code=404,
            detail={"codigo": "SOCIO_NO_ENCONTRADO", "mensaje": "No existe un Socio con ese ID"}
        )

    return socio
