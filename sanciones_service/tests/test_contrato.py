from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import yaml

from src.seed import seed_database
from src.models import Socio

CONTRATO = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/sanciones.openapi.yaml").read_text())


def validar(respuesta, ruta, metodo="get"):
    assert "date-time" in FormatChecker().checkers
    esquema = CONTRATO["paths"][ruta][metodo]["responses"][str(respuesta.status_code)]["content"]["application/json"]["schema"]
    Draft202012Validator({**esquema, "components": CONTRATO["components"]}, format_checker=FormatChecker()).validate(respuesta.json())


def test_respuestas_reales_sancionado_libre_y_desconocido_validan_contrato(client, db):
    seed_database(db)
    for rut in ("11.111.111-1", "22.222.222-2", "33.333.333-3"):
        respuesta = client.get(f"/v1/sanciones/socios/{rut}")
        assert respuesta.status_code == 200, respuesta.text
        validar(respuesta, "/v1/sanciones/socios/{identificador}")
        assert respuesta.json()["tiene_sancion"] == (rut == "22.222.222-2")
    validar(client.get("/v1/sanciones/socios"), "/v1/sanciones/socios")
    validar(client.get("/v1/sanciones/socios/no-existe"), "/v1/sanciones/socios/{identificador}")


def test_error_inesperado_conserva_500_con_error_estandar(client, db, monkeypatch):
    def fallar(*args, **kwargs):
        raise RuntimeError("Fallo real de consulta local")

    monkeypatch.setattr(db, "query", fallar)
    respuesta = client.get("/v1/sanciones/socios")
    assert respuesta.status_code == 500
    assert respuesta.json() == {"codigo": "ERROR_INTERNO", "mensaje": "Error interno del servicio"}
