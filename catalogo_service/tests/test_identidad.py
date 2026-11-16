from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Event, Lock

import grpc
import pytest

import catalogo_pb2 as pb
import catalogo_pb2_grpc as rpc
from src import server
from sqlalchemy import text


def reservar(stub, clave="reserva-1", libro=1):
    return stub.ReservarEjemplar(pb.ReservaRequest(libro_id=libro, operacion_id=clave), timeout=3)


def liberar(stub, ejemplar, reserva="reserva-1", operacion="liberacion-1"):
    return stub.LiberarEjemplar(pb.LiberarRequest(ejemplar_id=ejemplar, reserva_id=reserva, operacion_id=operacion), timeout=3)


def test_replay_concurrente_consume_una_copia(stub):
    with ThreadPoolExecutor(max_workers=20) as pool:
        resultados = list(pool.map(lambda _: reservar(stub), range(20)))
    assert all(r.exito for r in resultados)
    assert len({r.ejemplar_id for r in resultados}) == 1
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1


def test_colision_de_payload_no_cambia_reserva(stub):
    original = reservar(stub)
    with pytest.raises(grpc.RpcError) as error:
        reservar(stub, libro=2)
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert reservar(stub).ejemplar_id == original.ejemplar_id


def test_liberacion_vieja_no_suelta_reserva_nueva(stub):
    original = reservar(stub)
    primera = liberar(stub, original.ejemplar_id)
    assert primera.exito
    nueva = reservar(stub, "reserva-2")
    assert nueva.ejemplar_id == original.ejemplar_id
    assert liberar(stub, original.ejemplar_id) == primera
    assert liberar(stub, original.ejemplar_id, operacion="otra-liberacion-vieja").exito
    assert stub.ConsultarOperacion(pb.OperacionRequest(operacion_id="reserva-2"), timeout=2).estado == "RESERVADA"
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
    assert reservar(stub).estado == "LIBERADA"


def test_cancelacion_antes_de_reserva_impide_escritura_tardia(stub):
    request = pb.CancelacionRequest(operacion_id="cancelar-1", reserva_id="reserva-1", libro_id=1)
    assert stub.CancelarReserva(request, timeout=2).exito
    assert not reservar(stub).exito
    assert reservar(stub).estado == "CANCELADA"
    assert stub.CancelarReserva(request, timeout=2).exito
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2


def test_escrituras_sin_identidad_se_rechazan(stub):
    for metodo, request in [(stub.ReservarEjemplar, pb.ReservaRequest(libro_id=1)),
                            (stub.LiberarEjemplar, pb.LiberarRequest(ejemplar_id=1))]:
        with pytest.raises(grpc.RpcError) as error:
            metodo(request, timeout=2)
        assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_respuesta_perdida_no_consume_otra_copia(stub, monkeypatch):
    original = server.CatalogoServicer._escritura
    confirmada, responder = Event(), Event()
    lock, pendientes = Lock(), [True]

    def demorar_respuesta(self, request, context, *args):
        resultado = original(self, request, context, *args)
        with lock:
            primera = pendientes[0]
            pendientes[0] = False
        if primera:
            confirmada.set()
            assert responder.wait(5)
        return resultado

    monkeypatch.setattr(server.CatalogoServicer, "_escritura", demorar_respuesta)
    try:
        llamada = stub.ReservarEjemplar.future(pb.ReservaRequest(libro_id=1, operacion_id="reserva-1"), timeout=0.1)
        assert confirmada.wait(2)
        with pytest.raises(grpc.RpcError) as error:
            llamada.result()
        assert error.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        assert reservar(stub).ejemplar_id == 1
        assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
    finally:
        responder.set()


def test_sin_stock_es_resultado_terminal_y_liberacion_valida_payload(stub):
    a, b = reservar(stub, "a"), reservar(stub, "b")
    sin_stock = reservar(stub, "sin-stock")
    assert not sin_stock.exito
    liberar(stub, a.ejemplar_id, "a", "liberar-a")
    assert reservar(stub, "sin-stock") == sin_stock
    with pytest.raises(grpc.RpcError) as error:
        liberar(stub, b.ejemplar_id, "b", "liberar-a")
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    with pytest.raises(grpc.RpcError) as error:
        liberar(stub, b.ejemplar_id, "a", "propietario-incorrecto")
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS
    assert stub.ConsultarOperacion(pb.OperacionRequest(operacion_id="b"), timeout=2).estado == "RESERVADA"


def test_liberaciones_concurrentes_no_corrompen_propiedad(stub):
    original = reservar(stub)
    with ThreadPoolExecutor(max_workers=20) as pool:
        resultados = list(pool.map(lambda _: liberar(stub, original.ejemplar_id), range(20)))
    assert all(r == resultados[0] and r.exito for r in resultados)
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2
    assert reservar(stub, "nueva").ejemplar_id == original.ejemplar_id
    assert liberar(stub, original.ejemplar_id).exito
    assert stub.ConsultarOperacion(pb.OperacionRequest(operacion_id="nueva"), timeout=2).estado == "RESERVADA"


def test_rollback_de_liberacion_no_deja_resultado_falso(stub):
    original = reservar(stub)
    with server.SessionLocal() as db:
        db.execute(text("CREATE TRIGGER fallo_liberar BEFORE UPDATE ON ejemplares BEGIN SELECT RAISE(ABORT, 'fallo liberar'); END"))
        db.commit()
    with pytest.raises(grpc.RpcError) as error:
        liberar(stub, original.ejemplar_id)
    assert error.value.code() == grpc.StatusCode.INTERNAL
    assert stub.ConsultarOperacion(pb.OperacionRequest(operacion_id="reserva-1"), timeout=2).estado == "RESERVADA"
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
    with server.SessionLocal() as db:
        db.execute(text("DROP TRIGGER fallo_liberar"))
        db.commit()
    assert liberar(stub, original.ejemplar_id).exito


@contextmanager
def proceso_catalogo(base, log):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        puerto = sock.getsockname()[1]
    servicio = Path(__file__).resolve().parents[1]
    with log.open("ab") as output:
        proceso = subprocess.Popen([sys.executable, "main.py"], cwd=servicio,
            env={**os.environ, "DATABASE_URL": f"sqlite:///{base}", "GRPC_PORT": str(puerto)},
            stdout=output, stderr=subprocess.STDOUT)
        canal = grpc.insecure_channel(f"127.0.0.1:{puerto}")
        try:
            grpc.channel_ready_future(canal).result(timeout=8)
            yield rpc.CatalogoStub(canal)
        finally:
            canal.close()
            proceso.terminate()
            proceso.wait(timeout=8)


def test_migracion_legacy_y_reinicio_de_proceso_conservan_identidad(tmp_path):
    base, log = tmp_path / "legacy.db", tmp_path / "catalogo.log"
    with sqlite3.connect(base) as db:
        db.executescript("""
            CREATE TABLE libros (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, autor TEXT NOT NULL, genero TEXT NOT NULL);
            CREATE TABLE ejemplares (id INTEGER PRIMARY KEY, libro_id INTEGER NOT NULL, estado TEXT NOT NULL);
            INSERT INTO libros VALUES (1, 'Libro previo', 'Autor previo', 'OTRO');
            INSERT INTO ejemplares VALUES (1, 1, 'DISPONIBLE'), (2, 1, 'DISPONIBLE');
        """)
    with proceso_catalogo(base, log) as cliente:
        original = reservar(cliente)
    with proceso_catalogo(base, log) as cliente:
        assert reservar(cliente) == original
        assert cliente.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
        assert cliente.ObtenerLibro(pb.LibroRequest(libro_id=1), timeout=2).titulo == "Libro previo"
    with sqlite3.connect(base) as db:
        assert db.execute("SELECT count(*) FROM libros").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)
