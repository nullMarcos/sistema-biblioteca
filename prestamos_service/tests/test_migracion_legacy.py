from pathlib import Path
import runpy
import sqlite3

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src import models
from src.database import Base


def test_migracion_prestamos_anteriores_conserva_datos_y_no_inventa_propiedad(tmp_path):
    base = tmp_path / "anterior.db"
    with sqlite3.connect(base) as db:
        db.executescript("""
            CREATE TABLE socios (id INTEGER PRIMARY KEY, rut VARCHAR(12) NOT NULL UNIQUE,
                nombre VARCHAR(255) NOT NULL, correo VARCHAR(100) NOT NULL UNIQUE);
            CREATE TABLE prestamos (id INTEGER PRIMARY KEY, socio_id INTEGER NOT NULL, libro_id INTEGER NOT NULL,
                ejemplar_id INTEGER NOT NULL, fecha_prestamo DATETIME NOT NULL, fecha_limite DATE NOT NULL,
                fecha_devolucion DATETIME, estado VARCHAR(20) NOT NULL);
            CREATE TABLE saga_transacciones (id INTEGER PRIMARY KEY, saga_id VARCHAR(36) NOT NULL UNIQUE,
                idempotency_key VARCHAR(64) UNIQUE, socio_id INTEGER NOT NULL, libro_id INTEGER NOT NULL,
                ejemplar_id INTEGER, prestamo_id INTEGER, estado VARCHAR(30) NOT NULL, paso_actual VARCHAR(50) NOT NULL,
                respuesta_json TEXT, error_detalle TEXT, fecha_creacion DATETIME NOT NULL, fecha_actualizacion DATETIME NOT NULL);
            INSERT INTO socios VALUES (17, '77.777.777-7', 'Persona previa', 'previa@example.com');
            INSERT INTO prestamos VALUES (37, 17, 900, 901, '2026-10-01 12:30:00', '2026-10-15', NULL, 'ACTIVO');
            INSERT INTO saga_transacciones VALUES (7, 'anterior-completada', 'clave-previa', 17, 900, 901, 37,
                'COMPLETADA', 'REGISTRAR_PRESTAMO_LOCAL', NULL, NULL, '2026-10-01 12:30:00', '2026-10-01 12:30:00');
            INSERT INTO saga_transacciones VALUES (8, 'anterior-pendiente', 'clave-pendiente', 17, 902, NULL, NULL,
                'INICIADA', 'INICIO', NULL, NULL, '2026-10-01 12:30:00', '2026-10-01 12:30:00');
        """)
        socios_previos = db.execute("SELECT * FROM socios").fetchall()
        prestamos_previos = db.execute("SELECT * FROM prestamos").fetchall()
    raiz = Path(__file__).resolve().parents[2]
    aplicar = runpy.run_path(str(raiz / "scripts/migrar_sqlite.py"))["aplicar"]
    for _ in range(2):
        aplicar(base, raiz / "prestamos_service/migrations")
    engine = create_engine(f"sqlite:///{base}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            prestamo = db.get(models.Prestamo, 37)
            assert (prestamo.socio_id, prestamo.ejemplar_id, prestamo.estado) == (17, 901, "ACTIVO")
            completada, pendiente = db.scalars(select(models.SagaTransaccion).order_by(models.SagaTransaccion.id)).all()
            assert completada.reserva_id is None and not completada.cupo_reservado
            assert pendiente.reserva_id is None and pendiente.cupo_reservado
            assert pendiente.accion_pendiente == "CONCILIACION_LEGACY"
            assert db.query(models.DevolucionOperacion).count() == 0
        with sqlite3.connect(base) as db:
            assert db.execute("SELECT * FROM socios").fetchall() == socios_previos
            assert db.execute("SELECT * FROM prestamos").fetchall() == prestamos_previos
            assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (3,)
    finally:
        engine.dispose()
