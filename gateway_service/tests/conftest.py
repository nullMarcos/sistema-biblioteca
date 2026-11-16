from pathlib import Path
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "secreto-unitario-gateway-de-longitud-suficiente"

import main


@pytest.fixture
def client():
    main.peticiones_por_ip.clear()
    with TestClient(main.app, raise_server_exceptions=False) as cliente:
        yield cliente
