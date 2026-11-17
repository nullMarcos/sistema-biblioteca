from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from scripts.generar_openapi import contratos
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_contratos_estaticos_coinciden_con_fastapi_y_gateway_real():
    for nombre, documento in contratos().items():
        assert yaml.safe_load((ROOT / "contracts" / nombre).read_text()) == documento, nombre
        assert documento["openapi"] == "3.1.0"
        for ruta, item in documento["paths"].items():
            for metodo, operacion in item.items():
                if metodo.startswith("x-"):
                    continue
                assert "422" not in operacion["responses"], (nombre, ruta, metodo)
    internos = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text())
    publicos = yaml.safe_load((ROOT / "contracts/gateway.openapi.yaml").read_text())
    assert {"/api" + p for p in internos["paths"] if p.startswith("/v1/")} <= publicos["paths"].keys()
    assert not any("sanciones" in p or "chaos" in p for p in publicos["paths"])
    assert internos["paths"]["/v1/prestamos/{prestamo_id}"]["delete"]["deprecated"]
    bff = yaml.safe_load((ROOT / "contracts/bff.openapi.yaml").read_text())
    assert set(bff["paths"]) <= publicos["paths"].keys()
    assert not any("internal" in ruta or "health" in ruta for ruta in bff["paths"])


@pytest.mark.parametrize("nombre,modelo", [("openapi.yaml", "PrestamoInput"),
    ("bff.openapi.yaml", "SolicitudInput"), ("gateway.openapi.yaml", "PrestamoInput"),
    ("gateway.openapi.yaml", "SolicitudInput")])
def test_contrato_preserva_el_limite_entero_sqlite_sin_redondearlo(nombre, modelo):
    documento = yaml.safe_load((ROOT / "contracts" / nombre).read_text())
    esquema = documento["components"]["schemas"][modelo]["properties"]["socio_id"]
    validador = Draft202012Validator(esquema)
    assert validador.is_valid(2 ** 63 - 1)
    assert not validador.is_valid(2 ** 63)
