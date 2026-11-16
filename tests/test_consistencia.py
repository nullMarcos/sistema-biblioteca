import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.diagnosticar_consistencia import diagnosticar

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bases(tmp_path):
    rutas = [tmp_path / f"{s}.db" for s in ("catalogo", "prestamos", "sanciones")]
    for ruta, servicio in zip(rutas, ("catalogo", "prestamos", "sanciones")):
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{ruta}"}
        for _ in range(2):
            subprocess.run([sys.executable, "-m", "src.seed"], cwd=ROOT / f"{servicio}_service", env=env,
                check=True, capture_output=True, text=True)
    return rutas


def huellas(bases):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in bases]


def test_tres_semillas_arranque_repetido_y_diagnostico_solo_lectura(bases):
    antes = huellas(bases)
    resultado = diagnosticar(*bases)
    assert resultado["coherente"], resultado
    assert resultado["conteos"] == {"libros": 29, "ejemplares": 66, "prestamos_ocupantes": 0, "reservas_activas": 0}
    assert resultado["socios_correlacionados_por_rut"] == 3
    assert huellas(bases) == antes


def test_legacy_ambiguo_se_diagnostica_por_id_sin_liberarlo(bases):
    with sqlite3.connect(bases[0]) as db:
        db.execute("UPDATE ejemplares SET estado='PRESTADO' WHERE id=3")
    with sqlite3.connect(bases[1]) as db:
        db.execute("INSERT INTO prestamos (socio_id,libro_id,ejemplar_id,fecha_limite,estado) VALUES (1,1,3,'2030-01-01','ACTIVO')")
    antes = huellas(bases)
    resultado = diagnosticar(*bases)
    assert not resultado["coherente"]
    assert {d["codigo"] for d in resultado["discrepancias"]} == {
        "PRESTADO_SIN_RESERVA_IDENTIFICADA", "PRESTAMO_SIN_IDENTIDAD_UNIVOCA"}
    assert resultado["discrepancias"][0]["ejemplar_id"] == 3
    assert resultado["discrepancias"][0]["prestamos"] == [1]
    assert huellas(bases) == antes
    resultado_cli = subprocess.run([sys.executable, "scripts/diagnosticar_consistencia.py",
        "--catalogo", str(bases[0]), "--prestamos", str(bases[1]), "--sanciones", str(bases[2])],
        cwd=ROOT, capture_output=True, text=True)
    assert resultado_cli.returncode == 2, resultado_cli.stderr


def test_ids_locales_distintos_y_reserva_pendiente_identificable(bases):
    with sqlite3.connect(bases[0]) as db:
        db.execute("UPDATE ejemplares SET estado='PRESTADO' WHERE id=3")
        db.execute("INSERT INTO reserva_operaciones VALUES ('reserva',1,3,'RESERVADA','')")
    with sqlite3.connect(bases[1]) as db:
        db.execute("INSERT INTO saga_transacciones (saga_id,socio_id,libro_id,reserva_id,estado,paso_actual) "
            "VALUES ('saga',1,1,'reserva','EJEMPLAR_RESERVADO','VERIFICAR_SANCIONES_SOCIO')")
    with sqlite3.connect(bases[2]) as db:
        db.execute("UPDATE socios SET id=id+100")
        db.execute("UPDATE bloqueos SET socio_id=socio_id+100")
        db.execute("UPDATE multas SET socio_id=socio_id+100")
    resultado = diagnosticar(*bases)
    assert resultado["coherente"], resultado


def test_reserva_sin_propietario_no_pasa_como_inventario_coherente(bases):
    with sqlite3.connect(bases[0]) as db:
        db.execute("UPDATE ejemplares SET estado='PRESTADO' WHERE id=3")
        db.execute("INSERT INTO reserva_operaciones VALUES ('huerfana',1,3,'RESERVADA','')")
    resultado = diagnosticar(*bases)
    assert [d["codigo"] for d in resultado["discrepancias"]] == ["RESERVA_SIN_SAGA_PROPIETARIA_UNIVOCA"]


def test_ruta_inexistente_no_crea_base(tmp_path):
    ruta = tmp_path / "no-existe.db"
    with pytest.raises(sqlite3.OperationalError):
        diagnosticar(ruta, ruta, ruta)
    assert not ruta.exists()
