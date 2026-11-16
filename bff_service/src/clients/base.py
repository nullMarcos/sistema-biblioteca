import logging
import time

from src.errores import FalloDependencia
from src.resiliencia import Limitador

logger = logging.getLogger("bff.dependencias")


class ClienteLimitado:
    def __init__(self, servicio, maximo, circuito=None):
        self.servicio, self.circuito = servicio, circuito
        self.limite = Limitador(servicio, maximo)
        self.llamadas = self.red = self.fallbacks = 0
        self.ultima_duracion_ms = 0

    def tiempo(self, timeout, deadline):
        restante = min(timeout, deadline - time.monotonic())
        if restante <= 0:
            raise FalloDependencia(self.servicio, "TIMEOUT")
        return restante

    async def ejecutar(self, operacion, request_id, callback):
        inicio, adquirido, generacion, resultado = time.monotonic(), False, None, "ERROR"
        self.llamadas += 1
        try:
            if self.circuito is not None:
                generacion = self.circuito.admitir()
            self.limite.adquirir()
            adquirido = True
            respuesta = await callback()
            resultado = "DISPONIBLE"
            if self.circuito is not None:
                self.circuito.terminar(generacion, False)
            return respuesta
        except FalloDependencia as exc:
            resultado = exc.estado
            self.fallbacks += 1
            if self.circuito is not None and generacion is not None:
                fallo = exc.estado in {"TIMEOUT", "NO_DISPONIBLE", "RESPUESTA_INVALIDA"}
                self.circuito.terminar(generacion, None if exc.estado == "SATURADO" else fallo)
            raise
        except BaseException:
            if self.circuito is not None and generacion is not None:
                self.circuito.terminar(generacion)
            raise
        finally:
            if adquirido:
                self.limite.liberar()
            self.ultima_duracion_ms = round((time.monotonic() - inicio) * 1000, 3)
            logger.info("servicio=bff dependencia=%s operacion=%s request_id=%s resultado=%s duracion_ms=%s",
                self.servicio, operacion, request_id, resultado, self.ultima_duracion_ms)

    def metricas(self):
        return {"llamadas": self.llamadas, "llamadas_red": self.red, "fallos_o_fallbacks": self.fallbacks,
            "ultima_duracion_ms": self.ultima_duracion_ms, "bulkhead": self.limite.metricas(),
            "circuito": self.circuito.metricas() if self.circuito is not None else None}
