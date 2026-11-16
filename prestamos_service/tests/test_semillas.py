import pytest

from src import models
from src.seed import seed_database


def test_semilla_prestamos_no_asume_ids_de_otro_almacen(db):
    seed_database(db)
    assert db.query(models.Socio).count() == 3
    assert db.query(models.Prestamo).count() == 0
    anteriores = [(s.id, s.rut, s.nombre, s.correo) for s in db.query(models.Socio).order_by(models.Socio.id)]
    assert seed_database(db) == {"socios": 0, "prestamos": 0}
    assert [(s.id, s.rut, s.nombre, s.correo) for s in db.query(models.Socio).order_by(models.Socio.id)] == anteriores


def test_semilla_no_borra_socio_ni_historial_al_pedir_reset(db):
    db.add(models.Socio(id=70, rut="77.777.777-7", nombre="Persona existente", correo="previa@example.com"))
    db.add(models.SagaTransaccion(saga_id="legacy", socio_id=70, libro_id=90, estado="COMPENSACION_PENDIENTE",
        paso_actual="INICIO", cupo_reservado=True))
    db.commit()
    with pytest.raises(ValueError):
        seed_database(db, reset=True)
    assert db.query(models.Socio).one().nombre == "Persona existente"
    assert db.query(models.SagaTransaccion).one().cupo_reservado


def test_sagas_previas_impiden_poblar_una_base_parcial(db):
    db.add(models.SagaTransaccion(saga_id="legacy", socio_id=70, libro_id=90, estado="INICIADA", paso_actual="INICIO"))
    db.commit()
    assert seed_database(db) == {"socios": 0, "prestamos": 0}
    assert db.query(models.Socio).count() == 0
