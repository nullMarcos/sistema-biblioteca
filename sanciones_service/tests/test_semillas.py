from datetime import datetime, timezone

import pytest

from src.models import Socio, Bloqueo, Multa
from src.seed import seed_database


def test_semilla_repetida_conserva_estado_y_ruts(db):
    seed_database(db)
    assert {s.rut for s in db.query(Socio)} == {"11.111.111-1", "22.222.222-2", "33.333.333-3"}
    anterior = [(b.id, b.socio_id, b.fecha_inicio, b.fecha_fin, b.estado) for b in db.query(Bloqueo)]
    assert len(anterior) == 1
    assert seed_database(db) == {"socios": 0, "bloqueos": 0, "multas": 0}
    assert [(b.id, b.socio_id, b.fecha_inicio, b.fecha_fin, b.estado) for b in db.query(Bloqueo)] == anterior
    assert db.query(Multa).count() == 1


def test_reset_preserva_datos_anteriores(db):
    db.add(Socio(id=70, rut="77.777.777-7", nombre="Persona", correo="previa@example.com"))
    db.commit()
    with pytest.raises(ValueError):
        seed_database(db, reset=True)
    assert db.query(Socio).one().id == 70


def test_base_parcial_no_recibe_datos_ficticios(db):
    db.add(Bloqueo(id=80, socio_id=70, motivo="Anterior", fecha_inicio=datetime.now(timezone.utc), estado="ACTIVO"))
    db.commit()
    assert seed_database(db) == {"socios": 0, "bloqueos": 0, "multas": 0}
    assert db.query(Socio).count() == 0
