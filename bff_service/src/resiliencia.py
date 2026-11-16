import time

from src.errores import FalloDependencia


class Limitador:
    """Admisión inmediata por proceso/event loop; no acumula una cola de esperas."""

    def __init__(self, servicio, maximo):
        self.servicio, self.maximo = servicio, maximo
        self.activas = self.pico = self.rechazadas = 0

    def adquirir(self):
        if self.activas >= self.maximo:
            self.rechazadas += 1
            raise FalloDependencia(self.servicio, "SATURADO", headers={"Retry-After": "1"})
        self.activas += 1
        self.pico = max(self.pico, self.activas)

    def liberar(self):
        if self.activas <= 0:
            raise RuntimeError("Liberación sin admisión")
        self.activas -= 1

    def metricas(self):
        return {"maximo": self.maximo, "activas": self.activas, "pico": self.pico, "rechazadas": self.rechazadas}


class Circuito:
    """Estado por event loop, una sonda HALF_OPEN y generaciones para respuestas tardías."""

    def __init__(self, umbral, recuperacion, reloj=time.monotonic):
        self.umbral, self.recuperacion, self.reloj = umbral, recuperacion, reloj
        self.estado, self.fallos, self.generacion = "CLOSED", 0, 0
        self.sonda, self.rechazadas, self.cambio = False, 0, reloj()

    def _transicion(self, estado):
        self.estado, self.generacion, self.cambio = estado, self.generacion + 1, self.reloj()
        self.sonda = False

    def admitir(self):
        if self.estado == "OPEN" and self.reloj() - self.cambio >= self.recuperacion:
            self._transicion("HALF_OPEN")
        if self.estado == "OPEN" or (self.estado == "HALF_OPEN" and self.sonda):
            self.rechazadas += 1
            raise FalloDependencia("sanciones", "CIRCUITO_ABIERTO")
        if self.estado == "HALF_OPEN":
            self.sonda = True
        return self.generacion

    def terminar(self, generacion, fallo=None):
        if generacion != self.generacion:
            return
        self.sonda = False
        if fallo is None:  # Cancelación o saturación local: no demuestra salud remota.
            return
        if fallo:
            self.fallos += 1
            if self.estado == "HALF_OPEN" or self.fallos >= self.umbral:
                self._transicion("OPEN")
        else:
            self.fallos = 0
            if self.estado == "HALF_OPEN":
                self._transicion("CLOSED")

    def metricas(self):
        return {"estado": self.estado, "fallos": self.fallos, "umbral": self.umbral,
            "recuperacion_segs": self.recuperacion, "generacion": self.generacion,
            "sonda_activa": self.sonda, "rechazadas": self.rechazadas}
