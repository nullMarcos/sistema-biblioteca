from contextlib import AsyncExitStack

import grpc

from src.clients.catalogo import ClienteCatalogo
from src.clients.http import ClientePrestamos, ClienteSanciones
from src.resiliencia import Circuito, Limitador


class Recursos:
    def __init__(self, config):
        self.config = config
        self.stack = AsyncExitStack()
        self.peticiones = Limitador("bff", config.max_peticiones)

    async def __aenter__(self):
        try:
            self.prestamos = ClientePrestamos(self.config)
            self.stack.push_async_callback(self.prestamos.aclose)
            self.sanciones = ClienteSanciones(self.config, circuito=Circuito(
                self.config.sanciones_cb_threshold, self.config.sanciones_cb_recovery))
            self.stack.push_async_callback(self.sanciones.aclose)
            canal = await self.stack.enter_async_context(grpc.aio.insecure_channel(self.config.catalogo_target,
                options=(("grpc.enable_retries", 0), ("grpc.max_receive_message_length", self.config.max_respuesta_bytes))))
            self.catalogo = ClienteCatalogo(self.config, canal)
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    def metricas(self):
        return {"servicio": "bff", "alcance": "por proceso", "peticiones": self.peticiones.metricas(),
            "prestamos": self.prestamos.metricas(), "sanciones": self.sanciones.metricas(), "catalogo": self.catalogo.metricas(),
            "presupuestos": {"resumen_segs": self.config.resumen_timeout, "post_segs": self.config.post_timeout,
                "prestamos_lectura_segs": self.config.prestamos_timeout, "sanciones_segs": self.config.sanciones_timeout,
                "catalogo_segs": self.config.catalogo_timeout}, "reintentos": 0}
