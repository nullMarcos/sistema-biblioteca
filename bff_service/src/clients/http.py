import asyncio
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.clients.base import ClienteLimitado
from src.errores import FalloDependencia
from src.schemas import PrestamoRemoto, SancionesRemotas, SocioRemoto


class ErrorRemoto(BaseModel):
    model_config = ConfigDict(strict=True)
    codigo: str = Field(min_length=1, max_length=128)
    mensaje: str = Field(min_length=1, max_length=2048)


class ClienteHTTP(ClienteLimitado):
    def __init__(self, servicio, url, maximo, max_bytes, *, circuito=None, transport=None, api_key=None):
        super().__init__(servicio, maximo, circuito)
        self.max_bytes = max_bytes
        self.http = httpx.AsyncClient(base_url=url, trust_env=False, follow_redirects=False, transport=transport,
            limits=httpx.Limits(max_connections=maximo, max_keepalive_connections=maximo),
            headers={"Accept": "application/json", **({"X-API-Key": api_key} if api_key else {})})

    async def aclose(self):
        await self.http.aclose()

    async def pedir(self, metodo, ruta, timeout, deadline, headers, modelo, *, cuerpo=None, esperado=200):
        limite = self.tiempo(timeout, deadline)
        try:
            async with asyncio.timeout(limite):
                self.red += 1
                async with self.http.stream(metodo, ruta, headers=headers, json=cuerpo,
                        timeout=httpx.Timeout(limite, pool=min(0.05, limite))) as respuesta:
                    contenido = bytearray()
                    async for chunk in respuesta.aiter_bytes():
                        contenido.extend(chunk)
                        if len(contenido) > self.max_bytes:
                            raise FalloDependencia(self.servicio, "RESPUESTA_INVALIDA")
                    if respuesta.status_code != esperado:
                        try:
                            error = ErrorRemoto.model_validate_json(bytes(contenido))
                        except ValidationError as exc:
                            raise FalloDependencia(self.servicio, "RESPUESTA_INVALIDA") from exc
                        status = respuesta.status_code
                        if status not in {400, 401, 403, 404, 409, 429, 500, 502, 503, 504}:
                            raise FalloDependencia(self.servicio, "RESPUESTA_INVALIDA")
                        estado = {404: "NO_ENCONTRADO", 500: "NO_DISPONIBLE", 502: "RESPUESTA_INVALIDA",
                            503: "NO_DISPONIBLE", 504: "TIMEOUT", 429: "SATURADO"}.get(status, "RECHAZADA")
                        raise FalloDependencia(self.servicio, estado, error.mensaje, status_code=status, codigo=error.codigo,
                            headers={"Retry-After": respuesta.headers["Retry-After"]} if "Retry-After" in respuesta.headers else {})
                    try:
                        return modelo.model_validate_json(bytes(contenido))
                    except ValidationError as exc:
                        raise FalloDependencia(self.servicio, "RESPUESTA_INVALIDA") from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            estado = "SATURADO" if isinstance(exc, httpx.PoolTimeout) else "TIMEOUT"
            raise FalloDependencia(self.servicio, estado,
                "Resultado de escritura no confirmado; repita con la misma Idempotency-Key" if metodo == "POST" else None) from exc
        except httpx.RemoteProtocolError as exc:
            raise FalloDependencia(self.servicio, "RESPUESTA_INVALIDA") from exc
        except httpx.RequestError as exc:
            raise FalloDependencia(self.servicio, "NO_DISPONIBLE",
                "Comunicación de escritura interrumpida; repita con la misma Idempotency-Key" if metodo == "POST" else None) from exc


class ClientePrestamos(ClienteHTTP):
    def __init__(self, config, **kwargs):
        super().__init__("prestamos", config.prestamos_url, config.prestamos_max_concurrent,
            config.max_respuesta_bytes, api_key=config.api_key, **kwargs)
        self.config = config

    async def socio(self, socio_id, identidad, request_id, deadline):
        async def consultar():
            socio = await self.pedir("GET", f"/v1/socios/{socio_id}", self.config.prestamos_timeout,
                deadline, identidad.headers(request_id), SocioRemoto)
            if socio.id != socio_id:
                raise FalloDependencia("prestamos", "RESPUESTA_INVALIDA")
            return socio
        return await self.ejecutar("consultar_socio", request_id, consultar)

    async def solicitar(self, datos, clave, identidad, request_id, deadline):
        async def crear():
            prestamo = await self.pedir("POST", "/v1/prestamos", self.config.post_timeout, deadline,
                identidad.headers(request_id), PrestamoRemoto,
                cuerpo={**datos.model_dump(), "idempotency_key": clave}, esperado=201)
            if (prestamo.socio_id != datos.socio_id or prestamo.libro_id != datos.libro_id
                    or prestamo.idempotency_key != clave):
                raise FalloDependencia("prestamos", "RESPUESTA_INVALIDA",
                    "Respuesta de escritura no válida; consulte o repita con la misma Idempotency-Key")
            return prestamo
        return await self.ejecutar("solicitar_prestamo", request_id, crear)


class ClienteSanciones(ClienteHTTP):
    def __init__(self, config, **kwargs):
        super().__init__("sanciones", config.sanciones_url, config.sanciones_max_concurrent, 65536, **kwargs)
        self.config = config

    async def consultar(self, rut, request_id, deadline):
        async def consultar():
            respuesta = await self.pedir("GET", f"/v1/sanciones/socios/{quote(rut, safe='')}",
                self.config.sanciones_timeout, deadline, {"X-Request-Id": request_id}, SancionesRemotas)
            if respuesta.rut != rut:
                raise FalloDependencia("sanciones", "RESPUESTA_INVALIDA")
            return respuesta
        return await self.ejecutar("verificar_por_rut", request_id, consultar)
