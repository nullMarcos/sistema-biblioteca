import os
import sys
import tempfile
from concurrent import futures
from pathlib import Path

import grpc
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SERVICIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICIO))
sys.path.insert(0, str(SERVICIO / "protos"))
TEMPORAL = tempfile.TemporaryDirectory(prefix="biblioteca-catalogo-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{TEMPORAL.name}/importacion.db"

import catalogo_pb2_grpc
from src.database import Base, engine as engine_aplicacion
from src.models import Libro, Ejemplar
from src import server


@pytest.fixture(scope="session", autouse=True)
def limpiar_entorno():
    yield
    engine_aplicacion.dispose()
    TEMPORAL.cleanup()


@pytest.fixture
def stub(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/catalogo.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sesiones = sessionmaker(bind=engine)
    with sesiones() as db:
        db.add(Libro(id=1, titulo="Dune", autor="Frank Herbert", genero="SCI-FI",
            ejemplares=[Ejemplar(id=1, estado="DISPONIBLE"), Ejemplar(id=2, estado="DISPONIBLE")]))
        db.commit()
    monkeypatch.setattr(server, "SessionLocal", sesiones)
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    catalogo_pb2_grpc.add_CatalogoServicer_to_server(server.CatalogoServicer(), servidor)
    puerto = servidor.add_insecure_port("127.0.0.1:0")
    servidor.start()
    canal = grpc.insecure_channel(f"127.0.0.1:{puerto}")
    try:
        grpc.channel_ready_future(canal).result(timeout=3)
        yield catalogo_pb2_grpc.CatalogoStub(canal)
    finally:
        canal.close()
        servidor.stop(0).wait()
        engine.dispose()
