from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

import catalogo_pb2 as pb
import catalogo_pb2_grpc as rpc
import grpc

from procesos import RAIZ, catalogo, proceso, puerto_libre, esperar, http_listo


def test_matar_prestamos_despues_de_reservar_y_reiniciar_ambos_servicios(tmp_path):
    base_catalogo, base_prestamos = tmp_path / "catalogo.db", tmp_path / "prestamos.db"
    with sqlite3.connect(base_catalogo) as db:
        db.executescript("""
            CREATE TABLE libros (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, autor TEXT NOT NULL, genero TEXT NOT NULL);
            CREATE TABLE ejemplares (id INTEGER PRIMARY KEY, libro_id INTEGER NOT NULL, estado TEXT NOT NULL);
            INSERT INTO libros VALUES (1, 'Libro prueba', 'Autor prueba', 'OTRO');
            INSERT INTO ejemplares VALUES (1, 1, 'DISPONIBLE'), (2, 1, 'DISPONIBLE');
        """)
    with sqlite3.connect(base_prestamos) as db:
        db.executescript("""CREATE TABLE socios (id INTEGER PRIMARY KEY, rut TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL, correo TEXT NOT NULL UNIQUE);
            INSERT INTO socios VALUES (1, '11.111.111-1', 'Ana', 'ana@example.com');""")
    marcador, log = tmp_path / "reservado", tmp_path / "procesos.log"
    puerto_http = puerto_libre()
    with catalogo(base_catalogo, log) as puerto_grpc:
        env = {"DATABASE_URL": f"sqlite:///{base_prestamos}", "CATALOGO_GRPC_HOST": "127.0.0.1",
            "CATALOGO_GRPC_PORT": str(puerto_grpc), "CATALOGO_TIMEOUT_SEGS": "0.1", "CATALOGO_MAX_RETRIES": "0",
            "SAGA_LEASE_SECONDS": "3", "SQLITE_BUSY_TIMEOUT": "0.05", "API_KEY": "clave-reinicio"}
        codigo = f"""
from pathlib import Path
import time
import uvicorn
import main
from src import grpc_client
original = grpc_client.reservar_ejemplar
def reservar(*args):
    resultado = original(*args)
    Path({str(marcador)!r}).write_text('confirmada', encoding='utf-8')
    while True:
        time.sleep(1)
grpc_client.reservar_ejemplar = reservar
uvicorn.run(main.app, host='127.0.0.1', port={puerto_http}, log_level='error')
"""
        with proceso(["-c", codigo], "prestamos_service", env, log) as prestamos:
            esperar(lambda: http_listo(f"http://127.0.0.1:{puerto_http}/openapi.json"), prestamos)
            request = urllib.request.Request(f"http://127.0.0.1:{puerto_http}/v1/prestamos",
                data=json.dumps({"socio_id": 1, "libro_id": 1, "idempotency_key": "crash-real"}).encode(),
                headers={"Content-Type": "application/json", "X-API-Key": "clave-reinicio"})
            with ThreadPoolExecutor(max_workers=1) as pool:
                pendiente = pool.submit(urllib.request.urlopen, request, timeout=8)
                esperar(marcador.exists, prestamos)
                prestamos.kill()
                prestamos.wait(timeout=5)
                try:
                    respuesta = pendiente.result(timeout=5)
                except OSError:
                    pass
                else:
                    respuesta.close()
                    raise AssertionError("El proceso interrumpido no debe confirmar éxito")
        with sqlite3.connect(base_prestamos) as db:
            saga_id, lease, estado, ejemplar = db.execute("SELECT saga_id, lease_until, estado, ejemplar_id FROM saga_transacciones").fetchone()
            assert estado == "INICIADA" and ejemplar is None
        with sqlite3.connect(base_catalogo) as db:
            assert db.execute("SELECT estado FROM reserva_operaciones WHERE operacion_id=?", (saga_id,)).fetchone() == ("RESERVADA",)
    # Catálogo también se detuvo: el primer recuperador no inventa una cancelación.
    time.sleep(max(0, lease - time.time() + 0.05))

    def recuperar(*args):
        return subprocess.run([sys.executable, "-m", "src.saga.recuperacion", *args], cwd=RAIZ / "prestamos_service",
            env={**os.environ, **env}, capture_output=True, text=True, timeout=12)

    caida = recuperar()
    assert caida.returncode == 2, caida.stdout + caida.stderr
    assert json.loads(caida.stdout)["pendientes"] == 1
    with sqlite3.connect(base_prestamos) as db:
        assert db.execute("SELECT estado, cupo_reservado FROM saga_transacciones").fetchone() == ("COMPENSACION_PENDIENTE", 1)
    with catalogo(base_catalogo, log, puerto=puerto_grpc):
        recuperada = recuperar("--reanudar", "--saga-id", saga_id)
        assert recuperada.returncode == 0, recuperada.stdout + recuperada.stderr
        assert json.loads(recuperada.stdout)["resueltas"] == 1
        assert json.loads(recuperar().stdout)["procesadas"] == 0
        with grpc.insecure_channel(f"127.0.0.1:{puerto_grpc}") as canal:
            cliente = rpc.CatalogoStub(canal)
            assert cliente.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2
            assert cliente.ConsultarOperacion(pb.OperacionRequest(operacion_id=saga_id), timeout=2).estado == "CANCELADA"
        with proceso(["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(puerto_http), "--log-level", "error"],
                "prestamos_service", env, log) as reiniciado:
            esperar(lambda: http_listo(f"http://127.0.0.1:{puerto_http}/openapi.json"), reiniciado)
            request = urllib.request.Request(f"http://127.0.0.1:{puerto_http}/v1/prestamos/saga/idempotency/crash-real",
                headers={"X-API-Key": "clave-reinicio"})
            with urllib.request.urlopen(request, timeout=2) as respuesta:
                assert json.load(respuesta)["estado"] == "COMPENSADA"
    with sqlite3.connect(base_prestamos) as db:
        assert db.execute("SELECT count(*) FROM prestamos").fetchone() == (0,)


def test_matar_despues_de_liberar_recupera_devolucion_sin_afectar_reserva_posterior(tmp_path):
    base_catalogo, base_prestamos = tmp_path / "catalogo.db", tmp_path / "prestamos.db"
    with sqlite3.connect(base_catalogo) as db:
        db.executescript("""
            CREATE TABLE libros (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, autor TEXT NOT NULL, genero TEXT NOT NULL);
            CREATE TABLE ejemplares (id INTEGER PRIMARY KEY, libro_id INTEGER NOT NULL, estado TEXT NOT NULL);
            INSERT INTO libros VALUES (1, 'Libro prueba', 'Autor prueba', 'OTRO');
            INSERT INTO ejemplares VALUES (1, 1, 'DISPONIBLE'), (2, 1, 'DISPONIBLE');
        """)
    with sqlite3.connect(base_prestamos) as db:
        db.executescript("""CREATE TABLE socios (id INTEGER PRIMARY KEY, rut TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL, correo TEXT NOT NULL UNIQUE);
            INSERT INTO socios VALUES (1, '11.111.111-1', 'Ana', 'ana@example.com');""")
    marcador, log = tmp_path / "liberado", tmp_path / "procesos.log"
    puerto_http, puerto_sanciones = puerto_libre(), puerto_libre()
    args_http = ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(puerto_http), "--log-level", "error"]
    with catalogo(base_catalogo, log) as puerto_grpc, proceso(
            ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(puerto_sanciones), "--log-level", "error"],
            "sanciones_service", {"DATABASE_URL": f"sqlite:///{tmp_path / 'sanciones.db'}"}, log) as sanciones:
        esperar(lambda: http_listo(f"http://127.0.0.1:{puerto_sanciones}/health"), sanciones)
        env = {"DATABASE_URL": f"sqlite:///{base_prestamos}", "CATALOGO_GRPC_HOST": "127.0.0.1",
            "CATALOGO_GRPC_PORT": str(puerto_grpc), "CATALOGO_TIMEOUT_SEGS": "0.3", "CATALOGO_MAX_RETRIES": "0",
            "SAGA_LEASE_SECONDS": "3", "SQLITE_BUSY_TIMEOUT": "0.05", "API_KEY": "clave-reinicio",
            "SANCIONES_URL": f"http://127.0.0.1:{puerto_sanciones}"}
        codigo = f"""
from pathlib import Path
import time
import uvicorn
import main
from src import grpc_client
original = grpc_client.liberar_ejemplar
def liberar(*args):
    resultado = original(*args)
    Path({str(marcador)!r}).write_text('confirmada', encoding='utf-8')
    while True:
        time.sleep(1)
grpc_client.liberar_ejemplar = liberar
uvicorn.run(main.app, host='127.0.0.1', port={puerto_http}, log_level='error')
"""

        def request(ruta, body=None):
            return urllib.request.Request(f"http://127.0.0.1:{puerto_http}{ruta}",
                data=json.dumps(body).encode() if body is not None else None,
                headers={"X-API-Key": "clave-reinicio", "Content-Type": "application/json", "Idempotency-Key": "devolucion-real"})

        with proceso(["-c", codigo], "prestamos_service", env, log) as prestamos:
            esperar(lambda: http_listo(f"http://127.0.0.1:{puerto_http}/openapi.json"), prestamos)
            with urllib.request.urlopen(request("/v1/prestamos", {"socio_id": 1, "libro_id": 1, "idempotency_key": "alta-real"}), timeout=5) as respuesta:
                creado = json.load(respuesta)
                assert respuesta.status == 201 and creado["estado"] == "ACTIVO"
            ruta = f"/v1/prestamos/{creado['id']}/devolucion"
            with ThreadPoolExecutor(max_workers=1) as pool:
                pendiente = pool.submit(urllib.request.urlopen, request(ruta, {}), timeout=8)
                esperar(marcador.exists, prestamos)
                prestamos.kill()
                prestamos.wait(timeout=5)
                try:
                    respuesta = pendiente.result(timeout=5)
                except OSError:
                    pass
                else:
                    respuesta.close()
                    raise AssertionError("El proceso interrumpido no debe confirmar devolución")
        with sqlite3.connect(base_prestamos) as db:
            assert db.execute("SELECT estado FROM prestamos").fetchone() == ("DEVOLUCION_PENDIENTE",)
            lease = db.execute("SELECT lease_until FROM devolucion_operaciones").fetchone()[0]
        with grpc.insecure_channel(f"127.0.0.1:{puerto_grpc}") as canal:
            cliente = rpc.CatalogoStub(canal)
            posterior = cliente.ReservarEjemplar(pb.ReservaRequest(libro_id=1, operacion_id="posterior"), timeout=2)
            assert posterior.ejemplar_id == creado["ejemplar_id"]
            time.sleep(max(0, lease - time.time() + 0.05))
            recuperada = subprocess.run([sys.executable, "-m", "src.saga.recuperacion"], cwd=RAIZ / "prestamos_service",
                env={**os.environ, **env}, capture_output=True, text=True, timeout=12)
            assert recuperada.returncode == 0, recuperada.stdout + recuperada.stderr
            assert json.loads(recuperada.stdout)["resueltas"] == 1
            assert cliente.ConsultarOperacion(pb.OperacionRequest(operacion_id="posterior"), timeout=2).estado == "RESERVADA"
            assert cliente.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
        with proceso(args_http, "prestamos_service", env, log) as reiniciado:
            esperar(lambda: http_listo(f"http://127.0.0.1:{puerto_http}/openapi.json"), reiniciado)
            with urllib.request.urlopen(request(ruta, {}), timeout=2) as respuesta:
                assert json.load(respuesta)["estado"] == "DEVUELTO"
