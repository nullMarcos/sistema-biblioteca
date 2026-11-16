import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request

from prestamos_service.tests.procesos import catalogo, proceso, puerto_libre, esperar


def consultar(puerto, ruta):
    req = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", headers={"X-API-Key": "clave-legacy-desechable"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=0.5) as respuesta:
            return json.load(respuesta)
    except (OSError, urllib.error.URLError):
        return None


def test_servicios_reales_arrancan_dos_veces_sobre_datos_anteriores_sin_resembrar(tmp_path):
    cat, pre, san = [tmp_path / f"{s}.db" for s in ("catalogo", "prestamos", "sanciones")]
    with sqlite3.connect(cat) as db:
        db.executescript("""
            CREATE TABLE libros (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, autor TEXT NOT NULL, genero TEXT NOT NULL);
            CREATE TABLE ejemplares (id INTEGER PRIMARY KEY, libro_id INTEGER NOT NULL, estado TEXT NOT NULL);
            INSERT INTO libros VALUES (900,'Libro anterior','Autor anterior','OTRO');
            INSERT INTO ejemplares VALUES (901,900,'PRESTADO');
        """)
    tabla_socios = "CREATE TABLE socios (id INTEGER PRIMARY KEY, rut TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, correo TEXT UNIQUE NOT NULL);"
    with sqlite3.connect(pre) as db:
        db.executescript(tabla_socios + """
            CREATE TABLE prestamos (id INTEGER PRIMARY KEY, socio_id INTEGER NOT NULL, libro_id INTEGER NOT NULL,
                ejemplar_id INTEGER NOT NULL, fecha_prestamo DATETIME NOT NULL, fecha_limite DATE NOT NULL,
                fecha_devolucion DATETIME, estado TEXT NOT NULL);
            INSERT INTO socios VALUES (17,'77.777.777-7','Persona anterior','anterior@example.com');
            INSERT INTO prestamos VALUES (37,17,900,901,'2026-10-01 12:30:00','2026-10-15',NULL,'ACTIVO');
        """)
        original = db.execute("SELECT * FROM prestamos").fetchall()
    with sqlite3.connect(san) as db:
        db.executescript(tabla_socios + "INSERT INTO socios VALUES (117,'77.777.777-7','Persona anterior','anterior@example.com');")

    for intento in range(2):
        with catalogo(cat, tmp_path / f"catalogo-{intento}.log") as grpc_puerto:
            san_puerto, pre_puerto = puerto_libre(), puerto_libre()
            san_env = {"DATABASE_URL": f"sqlite:///{san}"}
            pre_env = {"DATABASE_URL": f"sqlite:///{pre}", "API_KEY": "clave-legacy-desechable",
                "CATALOGO_GRPC_HOST": "127.0.0.1", "CATALOGO_GRPC_PORT": str(grpc_puerto),
                "SANCIONES_URL": f"http://127.0.0.1:{san_puerto}"}
            comando = ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port"]
            with (
                proceso([*comando, str(san_puerto)], "sanciones_service", san_env, tmp_path / f"sanciones-{intento}.log") as s,
                proceso([*comando, str(pre_puerto)], "prestamos_service", pre_env, tmp_path / f"prestamos-{intento}.log") as p,
            ):
                esperar(lambda: consultar(san_puerto, "/openapi.json"), s)
                esperar(lambda: consultar(pre_puerto, "/openapi.json"), p)
                socio = consultar(pre_puerto, "/v1/socios/17")
                assert socio["rut"] == "77.777.777-7" and len(socio["prestamos"]) == 1
                assert consultar(pre_puerto, "/v1/prestamos/37")["estado"] == "ACTIVO"
                assert consultar(san_puerto, "/v1/sanciones/socios/77.777.777-7")["socio_id"] == 117
        with sqlite3.connect(pre) as db:
            assert db.execute("SELECT * FROM prestamos").fetchall() == original
            assert db.execute("SELECT count(*) FROM socios").fetchone() == (1,)
            assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (3,)
            assert db.execute("SELECT count(*) FROM saga_transacciones").fetchone() == (0,)
        with sqlite3.connect(cat) as db:
            assert db.execute("SELECT * FROM ejemplares").fetchall() == [(901, 900, "PRESTADO")]
            assert db.execute("SELECT count(*) FROM reserva_operaciones").fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)
