import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response

from src.agregacion import resumen
from src.identity import Identidad, identidad_gateway
from src.schemas import ClaveIdempotencia, ResumenOut, SolicitudInput, SolicitudOut

router = APIRouter(prefix="/bff/web", tags=["BFF web"])


async def recursos_admitidos(request: Request):
    recursos = request.app.state.recursos
    recursos.peticiones.adquirir()
    try:
        yield recursos
    finally:
        recursos.peticiones.liberar()


@router.get("/resumen-prestamos-socio/{socio_id}", response_model=ResumenOut,
    description="Compone Préstamos, Catálogo gRPC y Sanciones por RUT. Los fallos de fuentes opcionales producen 200 parcial con desconocidos null.")
async def resumen_socio(socio_id: Annotated[int, Path(gt=0)], request: Request,
        identidad: Identidad = Depends(identidad_gateway), recursos=Depends(recursos_admitidos)):
    identidad.autorizar(socio_id)
    return await resumen(recursos, socio_id, identidad, request.state.request_id, time.monotonic() + recursos.config.resumen_timeout)


@router.post("/solicitar-prestamo", response_model=SolicitudOut, status_code=201,
    description="Delega una sola escritura a la Saga de Préstamos. EN_REVISION registra una solicitud, no autoriza la entrega. Ante incertidumbre, repetir con la misma Idempotency-Key.")
async def solicitar_prestamo(datos: SolicitudInput, request: Request, response: Response,
        clave: Annotated[ClaveIdempotencia, Header(alias="Idempotency-Key")],
        identidad: Identidad = Depends(identidad_gateway), recursos=Depends(recursos_admitidos)):
    identidad.autorizar(datos.socio_id)
    prestamo = await recursos.prestamos.solicitar(datos, clave, identidad, request.state.request_id,
        time.monotonic() + recursos.config.post_timeout)
    response.headers["Idempotency-Key"] = clave
    return SolicitudOut(**prestamo.model_dump(), request_id=request.state.request_id)
