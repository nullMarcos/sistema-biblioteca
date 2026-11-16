from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from src import grpc_client, models
from src.routers import prestamos
from src.saga import PrestamoSagaOrchestrator
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba, SancionesFalsas

CONTRATO = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text())


def validar(respuesta, ruta, metodo):
    assert "date-time" in FormatChecker().checkers
    esquema = CONTRATO["paths"][ruta][metodo]["responses"][str(respuesta.status_code)]["content"]["application/json"]["schema"]
    Draft202012Validator({**esquema, "components": CONTRATO["components"]}, format_checker=FormatChecker()).validate(respuesta.json())


def test_respuestas_reales_alta_replay_consultas_y_devolucion_validan_contrato(client, db, monkeypatch):
    socio = socio_prueba(db)
    catalogo = CatalogoConEstado()
    monkeypatch.setattr(prestamos, "PrestamoSagaOrchestrator", lambda db: PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas()))
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", catalogo.liberar_ejemplar)
    respuesta = client.post("/v1/prestamos", json={"socio_id": socio, "libro_id": 1, "idempotency_key": "contrato"})
    assert respuesta.status_code == 201, respuesta.text
    validar(respuesta, "/v1/prestamos", "post")
    identificador = respuesta.json()["id"]
    saga = client.get("/v1/prestamos/saga/idempotency/contrato")
    validar(saga, "/v1/prestamos/saga/idempotency/{idempotency_key}", "get")
    validar(client.get(f"/v1/prestamos/saga/{saga.json()['saga_id']}"), "/v1/prestamos/saga/{saga_id}", "get")
    validar(client.get(f"/v1/socios/{socio}"), "/v1/socios/{socio_id}", "get")
    validar(client.get("/v1/prestamos"), "/v1/prestamos", "get")
    devuelta = client.post(f"/v1/prestamos/{identificador}/devolucion", headers={"Idempotency-Key": "dev-contrato"})
    assert devuelta.status_code == 200, devuelta.text
    validar(devuelta, "/v1/prestamos/{prestamo_id}/devolucion", "post")
    validar(client.get("/v1/prestamos/devoluciones/idempotency/dev-contrato"),
        "/v1/prestamos/devoluciones/idempotency/{idempotency_key}", "get")
    alias = client.delete(f"/v1/prestamos/{identificador}", headers={"Idempotency-Key": "dev-contrato"})
    validar(alias, "/v1/prestamos/{prestamo_id}", "delete")
    assert alias.json() == devuelta.json()


@pytest.mark.parametrize("estado", ["ACTIVO", "EN_REVISION", "DEVOLUCION_PENDIENTE", "DEVUELTO", "ANULADO", "COMPENSADO"])
def test_estados_persistidos_y_hateoas_validan_schema(client, db, estado):
    socio = socio_prueba(db)
    db.add(models.Prestamo(socio_id=socio, libro_id=1, ejemplar_id=1, fecha_limite=date.today(), estado=estado))
    db.commit()
    respuesta = client.get("/v1/prestamos/1")
    assert respuesta.status_code == 200
    validar(respuesta, "/v1/prestamos/{prestamo_id}", "get")
    acciones = respuesta.json()["_links"]
    assert ("devolver" in acciones) == (estado == "ACTIVO")
    assert ("cancelar" in acciones) == (estado == "EN_REVISION")


@pytest.mark.parametrize("error,codigo", [(grpc_client.CatalogoTimeoutError(), 504),
    (grpc_client.CatalogoNoDisponibleError(), 503), (grpc_client.CatalogoError("respuesta inválida"), 502)])
def test_causas_remotas_no_se_confunden_y_cumplen_schema(client, db, monkeypatch, error, codigo):
    class CatalogoFalla(CatalogoConEstado):
        def reservar_ejemplar(self, *args):
            raise error

    socio = socio_prueba(db)
    monkeypatch.setattr(prestamos, "PrestamoSagaOrchestrator", lambda db: PrestamoSagaOrchestrator(db, CatalogoFalla(), SancionesFalsas()))
    respuesta = client.post("/v1/prestamos", json={"socio_id": socio, "libro_id": 1})
    assert respuesta.status_code == codigo, respuesta.text
    validar(respuesta, "/v1/prestamos", "post")


def test_errores_locales_400_401_403_404_409_validan_contrato(client, db, monkeypatch):
    validar(client.post("/v1/prestamos", json={"socio_id": 0, "libro_id": 1}), "/v1/prestamos", "post")
    validar(client.get("/v1/socios", headers={"X-API-Key": "incorrecta"}), "/v1/socios", "get")
    monkeypatch.setenv("HABILITAR_FALLOS_DEMO", "false")
    validar(client.post("/v1/prestamos", json={"socio_id": 1, "libro_id": 1, "simular_fallo_paso3": True}), "/v1/prestamos", "post")
    validar(client.get("/v1/prestamos/999"), "/v1/prestamos/{prestamo_id}", "get")
    socio_prueba(db)
    existente = db.query(models.Socio).one()
    respuesta = client.post("/v1/socios", json={"rut": existente.rut, "nombre": existente.nombre, "correo": existente.correo})
    assert respuesta.status_code == 409
    validar(respuesta, "/v1/socios", "post")
