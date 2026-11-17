import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("variable,valor", [
    ("SAGA_LEASE_SECONDS", "nan"), ("SAGA_LEASE_SECONDS", "inf"),
    ("SAGA_RECUPERACION_BACKOFF", "inf"), ("SAGA_RECUPERACION_BACKOFF_MAX", "inf"),
])
def test_tiempos_no_finitos_se_rechazan_antes_de_admitir_operaciones(tmp_path, variable, valor):
    resultado = subprocess.run([sys.executable, "-c", "from src.saga import storage"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'no-usada.db'}",
            "SAGA_LEASE_SECONDS": "30", "SAGA_RECUPERACION_BACKOFF": "1",
            "SAGA_RECUPERACION_BACKOFF_MAX": "60", variable: valor})
    assert resultado.returncode != 0, f"Se aceptó {variable}={valor}"
    assert "RuntimeError" in resultado.stderr
