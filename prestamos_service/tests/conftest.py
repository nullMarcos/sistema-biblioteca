import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMPORAL = tempfile.TemporaryDirectory(prefix="biblioteca-prestamos-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{TEMPORAL.name}/importacion.db"
os.environ["API_KEY"] = "clave-unitaria-sin-datos-reales"

from main import app
from src.database import Base, get_db, engine as engine_aplicacion
from src import grpc_client


@pytest.fixture(scope="session", autouse=True)
def limpiar_entorno():
    yield
    grpc_client.cerrar_canal()
    engine_aplicacion.dispose()
    TEMPORAL.cleanup()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/prestamos.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine)() as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    def sesion_temporal():
        yield db

    def llamada_no_esperada(*args, **kwargs):
        raise AssertionError("Este caso no debe escribir en Catálogo")

    monkeypatch.setattr(grpc_client, "reservar_ejemplar", llamada_no_esperada)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", llamada_no_esperada)
    app.dependency_overrides[get_db] = sesion_temporal
    # Sin lifespan: no ejecutar semillas ni usar el engine de la aplicación.
    cliente = TestClient(app, headers={"X-API-Key": os.environ["API_KEY"]})
    try:
        yield cliente
    finally:
        cliente.close()
        app.dependency_overrides.clear()
