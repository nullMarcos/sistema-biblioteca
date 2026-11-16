from pathlib import Path

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
