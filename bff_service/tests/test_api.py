import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import crear_app
from src.config import Configuracion
from conftest import IDENTIDAD, prestamo

RUTA = "/bff/web/resumen-prestamos-socio/1"
POST = "/bff/web/solicitar-prestamo"


@pytest.mark.parametrize("opciones", [{"resumen_timeout": float("nan")}, {"sanciones_timeout": 0},
    {"catalogo_max_concurrent": 0}, {"prestamos_url": "http://usuario:clave@prestamos:8000"},
    {"sanciones_url": "file:///base.db"}, {"catalogo_target": "catalogo:99999"},
    {"post_timeout": 8}, {"catalogo_por_resumen": 9}, {"api_key": " "}])
def test_configuracion_invalida_falla_con_error_comprensible(opciones):
    with pytest.raises(ValidationError):
        Configuracion(**{"api_key": "clave", **opciones})


def test_health_real_inicializa_y_cierra_clientes_sin_consultar_fuentes(config):
    app = crear_app(config)
    with TestClient(app) as cliente:
        assert cliente.get("/health").json() == {"status": "ok", "service": "bff"}
        recursos = app.state.recursos
        assert recursos.prestamos.red == recursos.sanciones.red == recursos.catalogo.red == 0
        assert not recursos.prestamos.http.is_closed
    assert recursos.prestamos.http.is_closed and recursos.sanciones.http.is_closed


def test_resumen_separa_estados_deduplica_y_conserva_ids_y_fechas(client, fuentes):
    fuentes.socio["prestamos"] = [prestamo(5, 1), prestamo(2, 1), prestamo(3, 2, "EN_REVISION"),
        prestamo(6, 2, "DEVOLUCION_PENDIENTE"), prestamo(4, 3, "DEVUELTO"), prestamo(7, 4, "ANULADO")]
    respuesta = client.get(RUTA, headers={"X-Request-Id": "correlacion-distinta-de-clave"})
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert not datos["parcial"]
    assert datos["socio"] == {"id": 1, "nombre": "Socio de prueba"}
    assert [p["id"] for p in datos["prestamos_activos"]] == [2, 5]
    assert [p["id"] for p in datos["prestamos_en_revision"]] == [3]
    assert [p["id"] for p in datos["prestamos_pendientes"]] == [6]
    assert sorted(fuentes.libros) == [1, 2]
    assert datos["prestamos_activos"][0]["titulo"] == "Libro 1"
    assert datos["prestamos_activos"][0]["fecha_prestamo"] == "2026-11-16T12:00:00Z"
    assert datos["request_id"] == respuesta.headers["X-Request-Id"] == "correlacion-distinta-de-clave"
    assert fuentes.peticiones[1].url.path.endswith("/11.111.111-1")
    assert "rut" not in datos["socio"] and "correo" not in datos["socio"]
    assert "_links" not in respuesta.text


def test_socio_sin_prestamos_consulta_sanciones_sin_llamar_catalogo(client, fuentes):
    datos = client.get(RUTA).json()
    assert datos["prestamos_activos"] == datos["prestamos_en_revision"] == datos["prestamos_pendientes"] == []
    assert datos["sanciones"]["estado"] == "VERIFICADO" and datos["sanciones"]["tiene_sancion"] is False
    assert datos["dependencias"]["catalogo"] == "NO_CONSULTADO"
    assert not datos["parcial"] and len(fuentes.peticiones) == 2 and fuentes.libros == []


@pytest.mark.parametrize("status", [404, 500, 502, 503, 504])
def test_fallo_principal_es_error_y_no_llama_fuentes_opcionales(client, fuentes, status):
    fuentes.error_prestamos = status
    respuesta = client.get(RUTA)
    assert respuesta.status_code == status, respuesta.text
    assert respuesta.json()["codigo"] == "ERROR_REMOTO"
    assert respuesta.headers["Retry-After"] == "2"
    assert len(fuentes.peticiones) == 1 and fuentes.libros == []


@pytest.mark.parametrize("alteracion", ["sin-prestamos", "otro-propietario", "identidad-distinta", "fecha-naive"])
def test_schema_principal_invalido_no_produce_resumen(client, fuentes, alteracion):
    fuentes.socio["prestamos"] = [prestamo()]
    if alteracion == "sin-prestamos":
        del fuentes.socio["prestamos"]
    elif alteracion == "otro-propietario":
        fuentes.socio["prestamos"][0]["socio_id"] = 2
    elif alteracion == "identidad-distinta":
        fuentes.socio["id"] = 99
    else:
        fuentes.socio["prestamos"][0]["fecha_prestamo"] = "2026-11-16T12:00:00"
    respuesta = client.get(RUTA)
    assert respuesta.status_code == 502, respuesta.text
    assert len(fuentes.peticiones) == 1


@pytest.mark.parametrize("status,estado", [(404, "NO_REGISTRADO"), (500, "DESCONOCIDO"), (503, "DESCONOCIDO"), (504, "DESCONOCIDO")])
def test_sanciones_inaccesible_es_parcial_con_desconocidos_null(client, fuentes, status, estado):
    fuentes.error_sanciones = status
    respuesta = client.get(RUTA)
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["parcial"] and datos["sanciones"]["estado"] == estado
    assert all(datos["sanciones"][campo] is None for campo in ("tiene_sancion", "bloqueado", "multas_pendientes", "monto_total_multas"))
    assert datos["advertencias"][0]["codigo"].startswith("SANCIONES_")


@pytest.mark.parametrize("alteracion", [{"tiene_sancion": "false"}, {"rut": "22.222.222-2"}, {"bloqueado": True},
    {"monto_total_multas": -1}, {"multas_pendientes": 1}])
def test_respuesta_invalida_de_sanciones_nunca_habilita(client, fuentes, alteracion):
    fuentes.sanciones.update(alteracion)
    respuesta = client.get(RUTA)
    assert respuesta.status_code == 200
    assert respuesta.json()["dependencias"]["sanciones"] == "RESPUESTA_INVALIDA"
    assert respuesta.json()["sanciones"]["tiene_sancion"] is None


@pytest.mark.parametrize("status", [400, 401, 403, 409])
def test_consulta_rechazada_no_se_informa_como_dependencia_disponible(client, fuentes, status):
    fuentes.error_sanciones = status
    datos = client.get(RUTA).json()
    assert datos["parcial"] and datos["dependencias"]["sanciones"] == "RECHAZADA"
    assert datos["sanciones"]["estado"] == "DESCONOCIDO" and datos["sanciones"]["tiene_sancion"] is None


@pytest.mark.parametrize("estado", ["TIMEOUT", "NO_DISPONIBLE", "NO_ENCONTRADO", "SATURADO", "RESPUESTA_INVALIDA"])
def test_fallo_catalogo_preserva_prestamos_y_null_por_libro(client, fuentes, estado):
    fuentes.socio["prestamos"] = [prestamo(), prestamo(2, 2)]
    fuentes.fallos_libros[1] = estado
    datos = client.get(RUTA).json()
    assert datos["parcial"] and len(datos["prestamos_activos"]) == 2
    assert datos["prestamos_activos"][0]["libro_id"] == 1
    assert datos["prestamos_activos"][0]["titulo"] is datos["prestamos_activos"][0]["autor"] is None
    assert datos["prestamos_activos"][1]["titulo"] == "Libro 2"
    assert datos["advertencias"][0]["libro_id"] == 1 and datos["dependencias"]["catalogo"] == estado


def test_filtro_de_identidad_antes_de_consultar_y_sin_api_key_no_hay_acceso(client, fuentes):
    respuesta = client.get(RUTA, headers={"X-User-Role": "socio", "X-Socio-Id": "2"})
    assert respuesta.status_code == 403 and not fuentes.peticiones
    assert client.get(RUTA, headers={"X-API-Key": "incorrecta"}).status_code == 401
    assert client.get(RUTA, headers={"X-User-Id": ""}).status_code == 401
    assert client.get(RUTA, headers={"X-User-Role": "socio", "X-Socio-Id": ""}).status_code == 401
    assert not fuentes.peticiones


@pytest.mark.parametrize("clave", [None, "", "x" * 65, "con espacios", "/ruta"])
def test_post_exige_clave_valida_sin_efecto_remoto(client, fuentes, clave):
    respuesta = client.post(POST, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": clave} if clave is not None else {})
    assert respuesta.status_code == 400, respuesta.text
    assert not fuentes.peticiones


@pytest.mark.parametrize("datos", [{"socio_id": 0, "libro_id": 1}, {"socio_id": 1, "libro_id": -1},
    {"socio_id": "1", "libro_id": 1}, {"socio_id": True, "libro_id": 1},
    {"socio_id": 1, "libro_id": 1, "simular_fallo_paso3": True}])
def test_post_formato_invalido_y_control_de_caos_rechazados(client, fuentes, datos):
    assert client.post(POST, json=datos, headers={"Idempotency-Key": "validacion"}).status_code == 400
    assert not fuentes.peticiones


def test_post_delega_una_llamada_y_conserva_clave_actor_y_revision(client, fuentes):
    fuentes.creado = {**prestamo(estado="EN_REVISION"), "idempotency_key": "doble-clic"}
    respuesta = client.post(POST, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "doble-clic", "X-Request-Id": "correlacion"})
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["estado"] == "EN_REVISION"
    assert respuesta.json()["request_id"] == "correlacion" and respuesta.json()["idempotency_key"] == "doble-clic"
    assert respuesta.headers["Idempotency-Key"] == "doble-clic"
    assert len(fuentes.peticiones) == 1 and not fuentes.libros
    remota = fuentes.peticiones[0]
    assert remota.method == "POST" and remota.url.path == "/v1/prestamos"
    assert remota.headers["X-User-Id"] == IDENTIDAD["X-User-Id"] and remota.headers["X-Request-Id"] == "correlacion"
    assert json.loads(remota.content) == {"socio_id": 1, "libro_id": 1, "idempotency_key": "doble-clic"}
    assert "authorization" not in remota.headers and "_links" not in respuesta.text


@pytest.mark.parametrize("status", [400, 403, 404, 409, 429, 500, 502, 503, 504])
def test_post_conserva_causas_remotas_y_retry_after(client, fuentes, status):
    fuentes.error_prestamos = status
    respuesta = client.post(POST, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "misma-clave"})
    assert respuesta.status_code == status
    assert respuesta.json()["codigo"] == "ERROR_REMOTO" and respuesta.headers["Retry-After"] == "2"
    assert respuesta.headers["Idempotency-Key"] == "misma-clave" and len(fuentes.peticiones) == 1


@pytest.mark.parametrize("error,status", [(httpx.ReadTimeout("espera"), 504), (httpx.ConnectError("caída"), 503),
    (httpx.RemoteProtocolError("protocolo"), 502)])
def test_post_incierto_no_reintenta_ni_compensa(client, fuentes, error, status):
    fuentes.fallo_transporte = error
    respuesta = client.post(POST, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "incierta"})
    assert respuesta.status_code == status, respuesta.text
    assert len(fuentes.peticiones) == 1 and not fuentes.libros


def test_post_rechaza_respuesta_ajena_y_socio_sin_permiso(client, fuentes):
    fuentes.creado = {**prestamo(socio=2), "idempotency_key": "propietario"}
    assert client.post(POST, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "propietario"}).status_code == 502
    assert len(fuentes.peticiones) == 1
    assert client.post(POST, json={"socio_id": 2, "libro_id": 1}, headers={"Idempotency-Key": "otro", "X-User-Role": "socio", "X-Socio-Id": "1"}).status_code == 403
    assert len(fuentes.peticiones) == 1
