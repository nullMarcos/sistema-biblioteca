from pathlib import Path
import sys

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protos"))
import catalogo_pb2 as pb
import catalogo_pb2_grpc as rpc

from src.clients.base import ClienteLimitado
from src.errores import FalloDependencia


class ClienteCatalogo(ClienteLimitado):
    def __init__(self, config, canal):
        super().__init__("catalogo", config.catalogo_max_concurrent)
        self.config, self.stub = config, rpc.CatalogoStub(canal)

    async def libro(self, libro_id, request_id, deadline):
        async def consultar():
            try:
                limite = self.tiempo(self.config.catalogo_timeout, deadline)
                self.red += 1
                respuesta = await self.stub.ObtenerLibro(pb.LibroRequest(libro_id=libro_id), timeout=limite,
                    metadata=(("x-request-id", request_id),), wait_for_ready=False)
            except grpc.aio.AioRpcError as exc:
                estado = {grpc.StatusCode.NOT_FOUND: "NO_ENCONTRADO", grpc.StatusCode.DEADLINE_EXCEEDED: "TIMEOUT",
                    grpc.StatusCode.UNAVAILABLE: "NO_DISPONIBLE", grpc.StatusCode.RESOURCE_EXHAUSTED: "SATURADO"}.get(exc.code(), "RESPUESTA_INVALIDA")
                raise FalloDependencia("catalogo", estado) from exc
            if respuesta.id != libro_id or not respuesta.titulo.strip() or not respuesta.autor.strip():
                raise FalloDependencia("catalogo", "RESPUESTA_INVALIDA")
            return {"titulo": respuesta.titulo, "autor": respuesta.autor}
        return await self.ejecutar("obtener_libro", request_id, consultar)
