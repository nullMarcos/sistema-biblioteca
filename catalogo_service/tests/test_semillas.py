import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database import Base
from src.models import Libro, Ejemplar
from src.seed import seed_database


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/semilla.db")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_arranque_nuevo_no_inventa_ejemplares_prestados(db):
    seed_database(db)
    assert db.query(Libro).count() >= 9
    assert db.query(Ejemplar).filter_by(estado="PRESTADO").count() == 0
    anteriores = [(e.id, e.libro_id, e.estado) for e in db.query(Ejemplar).order_by(Ejemplar.id)]
    assert seed_database(db) == {"libros": 0, "ejemplares": 0}
    assert [(e.id, e.libro_id, e.estado) for e in db.query(Ejemplar).order_by(Ejemplar.id)] == anteriores


def test_catalogo_poblado_se_conserva_incluso_ante_reset(db):
    db.add(Libro(id=70, titulo="Dato existente", autor="Persona", genero="OTRO",
        ejemplares=[Ejemplar(id=80, estado="PRESTADO")]))
    db.commit()
    assert seed_database(db) == {"libros": 0, "ejemplares": 0}
    with pytest.raises(ValueError):
        seed_database(db, reset=True)
    assert db.query(Libro).one().titulo == "Dato existente"
    assert db.query(Ejemplar).one().estado == "PRESTADO"


def test_tabla_parcial_no_se_confunde_con_base_vacia(db):
    db.add(Ejemplar(id=80, libro_id=70, estado="PRESTADO"))
    db.commit()
    assert seed_database(db) == {"libros": 0, "ejemplares": 0}
    assert db.query(Libro).count() == 0
