from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verificar_gateway


def test_verificador_gateway_acumula_y_falla_el_comando(monkeypatch):
    contenedores = [{"Config": {"Labels": {"com.docker.compose.service": s}, "Env": ["RATE_LIMIT=2", "RATE_WINDOW=1"]},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "8080"}]} if s == "gateway" else {}},
        "HostConfig": {"PortBindings": {}}} for s in ("gateway", "catalogo", "prestamos", "sanciones", "bff")]
    monkeypatch.setattr(verificar_gateway, "compose", lambda *args, **kwargs: "1 2 3 4 5")
    monkeypatch.setattr(verificar_gateway.subprocess, "check_output", lambda *args, **kwargs: json.dumps(contenedores))
    monkeypatch.setattr(verificar_gateway.socket, "create_connection", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(verificar_gateway.time, "sleep", lambda *args: None)

    def api(puerto, ruta, token=None, cuerpo=None, **kwargs):
        if ruta == "/health":
            return 500, {"codigo": "FALLO", "mensaje": "Fallo probado"}
        if ruta == "/auth/login":
            return (200, {"access_token": "prueba"}) if cuerpo["password"] == "admin123" else (401, {})
        if ruta == "/api/v1/xyz":
            return 404, {}
        return (200 if token == "prueba" else 401), []

    monkeypatch.setattr(verificar_gateway, "api", api)
    lineas = []
    with pytest.raises(AssertionError, match="verificaciones fallaron"):
        verificar_gateway.verificar("prueba", {}, "http://127.0.0.1:8080", lineas.append)
    assert any("FAIL: health" in linea for linea in lineas)
    assert any("PASS: lectura autenticada" in linea for linea in lineas)


def test_shell_propaga_codigo_de_fallo_del_verificador(tmp_path):
    interprete = tmp_path / "python-falla"
    interprete.write_text("#!/bin/sh\nexit 7\n")
    interprete.chmod(0o700)
    resultado = subprocess.run(["bash", "scripts/test_gateway.sh"], cwd=ROOT, env={**os.environ, "PYTHON": str(interprete)}, capture_output=True)
    assert resultado.returncode == 7
