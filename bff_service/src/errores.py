class FalloDependencia(Exception):
    def __init__(self, servicio, estado, mensaje=None, *, status_code=None, codigo=None, headers=None):
        estados_http = {"NO_ENCONTRADO": 404, "TIMEOUT": 504, "RESPUESTA_INVALIDA": 502}
        self.servicio, self.estado = servicio, estado
        self.status_code = status_code or estados_http.get(estado, 503)
        self.codigo = codigo or f"{servicio.upper()}_{estado}"
        self.mensaje = mensaje or f"La consulta a {servicio} no pudo completarse ({estado})"
        self.headers = headers or {}
        super().__init__(self.mensaje)
