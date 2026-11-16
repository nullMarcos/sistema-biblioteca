import os
from pathlib import Path
import sys
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMPORAL = tempfile.TemporaryDirectory(prefix="biblioteca-sanciones-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{TEMPORAL.name}/importacion.db"

from src.database import Base, engine as engine_aplicacion
from src import models
from main import app
from src.database import get_db


@pytest.fixture(scope="session", autouse=True)
def limpiar_entorno():
    yield
    engine_aplicacion.dispose()
    TEMPORAL.cleanup()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/sanciones.db")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def client(db):
    def sesion_temporal():
        yield db

    app.dependency_overrides[get_db] = sesion_temporal
    cliente = TestClient(app, raise_server_exceptions=False)
    try:
        yield cliente
    finally:
        cliente.close()
        app.dependency_overrides.clear()
