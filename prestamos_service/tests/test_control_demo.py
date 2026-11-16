import pytest

from src import models
from src.routers import prestamos
from src.saga import PrestamoSagaOrchestrator
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba, SancionesFalsas


@pytest.mark.parametrize("habilitado,actor,rol", [(False, "1", "admin"), (True, "2", "socio"), (True, "", "admin")])
def test_inyeccion_de_fallo_requiere_habilitacion_y_administrador(client, db, monkeypatch, habilitado, actor, rol):
    monkeypatch.setenv("HABILITAR_FALLOS_DEMO", str(habilitado).lower())
    respuesta = client.post("/v1/prestamos", headers={"X-User-ID": actor, "X-User-Role": rol},
        json={"socio_id": 1, "libro_id": 1, "simular_fallo_paso3": True})
    assert respuesta.status_code == 403, respuesta.text
    assert set(respuesta.json()) == {"codigo", "mensaje"}
    assert db.query(models.SagaTransaccion).count() == 0


def test_demo_autorizada_falla_y_compensa_sin_confirmar_prestamo(client, db, monkeypatch):
    socio = socio_prueba(db)
    catalogo = CatalogoConEstado()
    monkeypatch.setenv("HABILITAR_FALLOS_DEMO", "true")
    monkeypatch.setattr(prestamos, "PrestamoSagaOrchestrator",
        lambda db: PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas()))
    respuesta = client.post("/v1/prestamos", headers={"X-User-ID": "1", "X-User-Role": "admin"},
        json={"socio_id": socio, "libro_id": 1, "simular_fallo_paso3": True})
    assert respuesta.status_code == 500, respuesta.text
    assert respuesta.json()["codigo"] == "FALLO_PASO_COMPENSADO"
    assert db.query(models.Prestamo).count() == 0
    assert db.query(models.SagaTransaccion).one().estado == "COMPENSADA"
    assert catalogo.acciones == ["reservar", "cancelar"]
