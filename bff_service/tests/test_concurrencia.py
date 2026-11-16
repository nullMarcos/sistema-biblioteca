import asyncio
import time

import httpx

from main import crear_app
from conftest import IDENTIDAD, RecursosPrueba, prestamo, sanciones


def test_sanciones_lenta_no_bloquea_otro_resumen_y_el_limite_es_global(config, fuentes):
    async def caso():
        iniciada, terminar = asyncio.Event(), asyncio.Event()
        async def sanciones_lenta(request):
            iniciada.set()
            await terminar.wait()
            return httpx.Response(200, json=sanciones())
        fuentes.http_sanciones = sanciones_lenta
        fuentes.socio["prestamos"] = [prestamo()]
        conf = config.model_copy(update={"sanciones_max_concurrent": 1})
        app = crear_app(conf, lambda c: RecursosPrueba(c, fuentes))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://bff", headers=IDENTIDAD) as client:
                primera = asyncio.create_task(client.get("/bff/web/resumen-prestamos-socio/1"))
                try:
                    await asyncio.wait_for(iniciada.wait(), 1)
                    inicio = time.monotonic()
                    segunda = await client.get("/bff/web/resumen-prestamos-socio/1")
                    assert segunda.status_code == 200 and segunda.json()["prestamos_activos"][0]["titulo"] == "Libro 1"
                    assert segunda.json()["parcial"] and segunda.json()["dependencias"]["sanciones"] == "SATURADO"
                    assert time.monotonic() - inicio < 0.5 and not primera.done()
                finally:
                    terminar.set()
                    respuesta = await primera
                assert respuesta.status_code == 200 and not respuesta.json()["parcial"]
                metricas = fuentes.recursos.sanciones.metricas()
                assert metricas["bulkhead"]["pico"] == metricas["llamadas_red"] == 1
                assert metricas["bulkhead"]["rechazadas"] == 1 and metricas["bulkhead"]["activas"] == 0
    asyncio.run(caso())


def test_presupuesto_total_de_resumen_se_reparte_entre_fuentes(config, fuentes):
    async def caso():
        async def lento(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json=sanciones())
        fuentes.http_sanciones = lento
        conf = config.model_copy(update={"resumen_timeout": 0.08, "prestamos_timeout": 0.03})
        app = crear_app(conf, lambda c: RecursosPrueba(c, fuentes))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://bff", headers=IDENTIDAD) as client:
                inicio = time.monotonic()
                respuesta = await client.get("/bff/web/resumen-prestamos-socio/1")
                assert respuesta.status_code == 200 and respuesta.json()["dependencias"]["sanciones"] == "TIMEOUT"
                assert time.monotonic() - inicio < 0.4
                assert fuentes.recursos.sanciones.limite.activas == 0
    asyncio.run(caso())


def test_admision_bff_rechaza_exceso_sin_colas(config, fuentes):
    async def caso():
        iniciada, terminar = asyncio.Event(), asyncio.Event()
        async def lento(request):
            iniciada.set()
            await terminar.wait()
            return httpx.Response(200, json=fuentes.socio)
        fuentes.http_prestamos = lento
        conf = config.model_copy(update={"max_peticiones": 1})
        app = crear_app(conf, lambda c: RecursosPrueba(c, fuentes))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://bff", headers=IDENTIDAD) as client:
                primera = asyncio.create_task(client.get("/bff/web/resumen-prestamos-socio/1"))
                try:
                    await asyncio.wait_for(iniciada.wait(), 1)
                    respuesta = await client.get("/bff/web/resumen-prestamos-socio/1")
                    assert respuesta.status_code == 503 and respuesta.json()["codigo"] == "BFF_SATURADO"
                    assert respuesta.headers["Retry-After"] == "1"
                finally:
                    terminar.set()
                    await primera
                assert fuentes.recursos.peticiones.activas == 0 and fuentes.recursos.peticiones.pico == 1
    asyncio.run(caso())
