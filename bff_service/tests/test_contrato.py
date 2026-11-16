from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from conftest import prestamo

CONTRATO = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/bff.openapi.yaml").read_text())


def validar(respuesta, ruta, metodo):
    esquema = CONTRATO["paths"][ruta][metodo]["responses"][str(respuesta.status_code)]["content"]["application/json"]["schema"]
    Draft202012Validator({**esquema, "components": CONTRATO["components"]}, format_checker=FormatChecker()).validate(respuesta.json())


def test_contrato_publico_solo_expone_dos_operaciones_jwt_y_clave_obligatoria():
    assert set(CONTRATO["paths"]) == {"/bff/web/resumen-prestamos-socio/{socio_id}", "/bff/web/solicitar-prestamo"}
    assert CONTRATO["components"]["securitySchemes"] == {"HTTPBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
    post = CONTRATO["paths"]["/bff/web/solicitar-prestamo"]["post"]
    assert next(p for p in post["parameters"] if p["name"] == "Idempotency-Key")["required"]
    assert post["security"] == [{"HTTPBearer": []}]
    for ruta in CONTRATO["paths"].values():
        for operacion in ruta.values():
            assert not any(p["name"].lower().startswith(("x-api-", "x-user-", "x-socio-")) for p in operacion.get("parameters", []))
    assert "date-time" in FormatChecker().checkers


@pytest.mark.parametrize("fallo", [None, 404, 503, 504])
def test_resumen_real_y_parcial_validan_schema_y_fechas(client, fuentes, fallo):
    fuentes.socio["prestamos"] = [prestamo(), prestamo(2, 2, "EN_REVISION")]
    fuentes.error_sanciones = fallo
    respuesta = client.get("/bff/web/resumen-prestamos-socio/1")
    assert respuesta.status_code == 200
    validar(respuesta, "/bff/web/resumen-prestamos-socio/{socio_id}", "get")


def test_post_y_errores_reales_validan_contrato(client, fuentes):
    ruta = "/bff/web/solicitar-prestamo"
    validar(client.post(ruta, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "contrato"}), ruta, "post")
    validar(client.post(ruta, json={"socio_id": 1, "libro_id": 1}), ruta, "post")
    for status in (400, 403, 404, 409, 429, 500, 502, 503, 504):
        fuentes.error_prestamos = status
        validar(client.post(ruta, json={"socio_id": 1, "libro_id": 1}, headers={"Idempotency-Key": "contrato"}), ruta, "post")
