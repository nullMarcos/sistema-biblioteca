# Codigo para comunicarse con el mamañema de Gabriel Castillo Castillo
import os
import grpc

from protos import catalogo_pb2, catalogo_pb2_grpc

CATALOGO_HOST = os.getenv("CATALOGO_GRPC_HOST", "localhost")
CATALOGO_PORT = os.getenv("CATALOGO_GRPC_PORT", "50051")
TIME_OUT_SEGS = 2.0

canal = grpc.insecure_channel(f"{CATALOGO_HOST}:{CATALOGO_PORT}")
stub = catalogo_pb2_grpc.CatalogoStub(canal)

class SinStockError(Exception):
    pass

class LiberacionFallidaError(Exception):
    pass

class CatalogoNoDisponibleError(Exception):
    pass

def reservar_ejemplar(libro_id: int):
    request = catalogo_pb2.ReservaRequest(libro_id=libro_id)

    try:
        response = stub.ReservarEjemplar(request, timeout=TIME_OUT_SEGS)
    except grpc.RpcError as e:
        raise CatalogoNoDisponibleError(e.details()) from e

    if not response.exito:
        raise SinStockError(response.motivo)

    return response.ejemplar_id

def liberar_ejemplar(ejemplar_id: int):
    request = catalogo_pb2.LiberarRequest(ejemplar_id=ejemplar_id)

    try:
        response = stub.LiberarEjemplar(request, timeout=TIME_OUT_SEGS)
    except grpc.RpcError as e:
        raise CatalogoNoDisponibleError(e.details()) from e

    if not response.exito:
        raise LiberacionFallidaError(response.motivo)

def cerrar_canal():
    canal.close()