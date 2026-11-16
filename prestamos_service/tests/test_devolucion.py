from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from src import models, grpc_client
from src.schemas import PrestamoInput
from src.saga import PrestamoSagaOrchestrator, recuperacion
from src.saga.exceptions import SagaError
from test_persistencia_saga import CatalogoConEstado
from test_reglas_entrada import socio_prueba, SancionesFalsas
from src.saga import admission


def prestar(db, revision=False):
    class Verificador(SancionesFalsas):
        def verificar_sanciones(self, rut):
            return {"tiene_sancion": None if revision else False, "degradado": revision}
    catalogo = CatalogoConEstado()
    prestamo, _ = PrestamoSagaOrchestrator(db, catalogo, Verificador()).ejecutar(
        PrestamoInput(socio_id=socio_prueba(db), libro_id=1, idempotency_key="crear"))
    return prestamo.id, catalogo


@pytest.mark.parametrize("metodo", ["post", "delete"])
def test_devolucion_y_replay_comparten_caso_de_uso(client, db, monkeypatch, metodo):
    identificador, catalogo = prestar(db)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", catalogo.liberar_ejemplar)
    url = f"/v1/prestamos/{identificador}" + ("/devolucion" if metodo == "post" else "")
    response = getattr(client, metodo)(url, headers={"Idempotency-Key": "devolver"})
    assert response.status_code == 200
    assert response.json()["estado"] == "DEVUELTO"
    assert response.json()["fecha_devolucion"]
    assert "devolver" not in response.json()["_links"]
    assert getattr(client, metodo)(url, headers={"Idempotency-Key": "devolver"}).json() == response.json()
    assert getattr(client, metodo)(url).status_code == 409
    assert catalogo.acciones == ["reservar", "cancelar"]
    assert db.query(models.DevolucionOperacion).count() == 1
    auditada = client.get("/v1/prestamos/devoluciones/idempotency/devolver")
    assert auditada.status_code == 200
    assert auditada.json()["estado"] == "COMPLETADA"


def test_hateoas_y_cancelacion_en_revision_no_aprueba(client, db, monkeypatch):
    identificador, catalogo = prestar(db, revision=True)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", catalogo.liberar_ejemplar)
    data = client.get(f"/v1/prestamos/{identificador}").json()
    assert data["_links"]["cancelar"] == {"href": f"/v1/prestamos/{identificador}/devolucion", "method": "POST"}
    assert "devolver" not in data["_links"]
    response = client.post(f"/v1/prestamos/{identificador}/devolucion")
    assert response.status_code == 200
    assert response.json()["estado"] == "ANULADO"
    assert response.json()["fecha_devolucion"] is None
    assert "cancelar" not in response.json()["_links"]
    assert client.delete(f"/v1/prestamos/{identificador}").status_code == 409


@pytest.mark.parametrize("estado", ["ANULADO", "COMPENSADO"])
def test_transicion_invalida_no_libera(client, db, estado):
    identificador, _ = prestar(db)
    db.get(models.Prestamo, identificador).estado = estado
    db.commit()
    response = client.post(f"/v1/prestamos/{identificador}/devolucion")
    assert response.status_code == 409
    assert response.json()["codigo"] == "ESTADO_PRESTAMO_INVALIDO"


@pytest.mark.parametrize("misma_clave", [True, False])
def test_devolucion_concurrente_solo_libera_una_vez(db, misma_clave):
    from src.saga.devolucion import DevolucionService

    identificador, catalogo = prestar(db)
    factory = sessionmaker(bind=db.get_bind())
    db.rollback()
    inicio = Barrier(10)

    def devolver(i):
        with factory() as sesion:
            inicio.wait(timeout=5)
            try:
                return DevolucionService(sesion, catalogo).devolver(identificador,
                    "misma" if misma_clave else f"distinta-{i}").estado
            except SagaError as exc:
                return exc.codigo

    with ThreadPoolExecutor(max_workers=10) as pool:
        resultados = list(pool.map(devolver, range(10)))
    assert "DEVUELTO" in resultados
    assert set(resultados) <= {"DEVUELTO", "DEVOLUCION_PENDIENTE", "PRESTAMO_YA_DEVUELTO"}
    assert catalogo.acciones == ["reservar", "cancelar"]
    assert db.query(models.DevolucionOperacion).count() == 1


def test_fallo_finalizar_despues_de_liberar_se_recupera_sin_reservar(client, db, monkeypatch):
    identificador, catalogo = prestar(db)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", catalogo.liberar_ejemplar)
    db.execute(text("""CREATE TRIGGER fallo_devolucion BEFORE UPDATE ON prestamos
        WHEN NEW.estado='DEVUELTO' BEGIN SELECT RAISE(ABORT, 'fallo real después de liberar'); END"""))
    db.commit()
    response = client.post(f"/v1/prestamos/{identificador}/devolucion", headers={"Idempotency-Key": "caida"})
    assert response.status_code == 503
    assert client.get(f"/v1/prestamos/{identificador}").json()["estado"] == "DEVOLUCION_PENDIENTE"
    assert not any(catalogo.activas.values())
    operacion = db.query(models.DevolucionOperacion).one()
    operacion_id = operacion.operacion_id
    assert operacion.estado == "PENDIENTE"
    assert operacion.accion_pendiente == "FINALIZAR_DEVOLUCION"
    db.execute(text("DROP TRIGGER fallo_devolucion"))
    db.commit()
    assert recuperacion.recuperar(sessionmaker(bind=db.get_bind()), grpc_client=catalogo,
        reanudar=True, devolucion_id=operacion_id)["resueltas"] == 1
    db.expire_all()
    repetido = client.post(f"/v1/prestamos/{identificador}/devolucion", headers={"Idempotency-Key": "caida"})
    assert repetido.status_code == 200
    assert repetido.json()["estado"] == "DEVUELTO"
    assert catalogo.acciones == ["reservar", "cancelar"]


def test_clave_de_devolucion_asociada_a_prestamo_y_actor(client, db, monkeypatch):
    identificador, catalogo = prestar(db)
    monkeypatch.setattr(grpc_client, "liberar_ejemplar", catalogo.liberar_ejemplar)
    url = f"/v1/prestamos/{identificador}/devolucion"
    assert client.post(url, headers={"Idempotency-Key": "actor", "X-User-ID": "uno", "X-User-Role": "admin"}).status_code == 200
    conflicto = client.post(url, headers={"Idempotency-Key": "actor", "X-User-ID": "dos", "X-User-Role": "admin"})
    assert conflicto.status_code == 409
    assert conflicto.json()["codigo"] == "IDEMPOTENCIA_PAYLOAD_DISTINTO"
    otro = client.post("/v1/prestamos/987654/devolucion", headers={"Idempotency-Key": "actor", "X-User-ID": "uno", "X-User-Role": "admin"})
    assert otro.status_code == 409


@pytest.mark.parametrize("metodo", ["post", "delete"])
def test_timeout_despues_de_liberar_retiene_cupo_y_se_concilia(client, db, monkeypatch, metodo):
    identificador, catalogo = prestar(db)
    monkeypatch.setattr(admission, "MAX_PRESTAMOS_POR_SOCIO", 1)

    def respuesta_perdida(*args):
        assert not db.connection().connection.driver_connection.in_transaction
        catalogo.liberar_ejemplar(*args)
        raise grpc_client.CatalogoTimeoutError("Respuesta perdida tras confirmar liberación")

    monkeypatch.setattr(grpc_client, "liberar_ejemplar", respuesta_perdida)
    url = f"/v1/prestamos/{identificador}" + ("/devolucion" if metodo == "post" else "")
    response = getattr(client, metodo)(url, headers={"Idempotency-Key": "incierta"})
    assert response.status_code == 504
    assert response.json()["codigo"] == "OPERACION_PENDIENTE"
    operacion = db.query(models.DevolucionOperacion).one()
    operacion_id = operacion.operacion_id
    assert operacion.accion_pendiente == "LIBERAR_EJEMPLAR"
    prestamo = db.get(models.Prestamo, identificador)
    assert prestamo.estado == "DEVOLUCION_PENDIENTE"
    with pytest.raises(SagaError) as error:
        PrestamoSagaOrchestrator(db, catalogo, SancionesFalsas()).ejecutar(
            PrestamoInput(socio_id=prestamo.socio_id, libro_id=2, idempotency_key="otro-libro"))
    assert error.value.codigo == "CUPO_PRESTAMOS_AGOTADO"
    assert recuperacion.recuperar(sessionmaker(bind=db.get_bind()), grpc_client=catalogo,
        reanudar=True, devolucion_id=operacion_id)["resueltas"] == 1
    db.expire_all()
    assert db.get(models.Prestamo, identificador).estado == "DEVUELTO"


def test_devolucion_legacy_sin_propietario_requiere_conciliacion(client, db):
    identificador, _ = prestar(db)
    db.query(models.SagaTransaccion).one().reserva_id = None
    db.commit()
    response = client.post(f"/v1/prestamos/{identificador}/devolucion")
    assert response.status_code == 409
    assert response.json()["codigo"] == "RESERVA_SIN_IDENTIDAD"


@pytest.mark.parametrize("clave", ["", "con espacio", "x" * 65])
def test_clave_invalida_no_inicia_devolucion(client, db, clave):
    identificador, _ = prestar(db)
    response = client.post(f"/v1/prestamos/{identificador}/devolucion", headers={"Idempotency-Key": clave})
    assert response.status_code == 400
    assert db.query(models.DevolucionOperacion).count() == 0


def test_contrato_de_devolucion_coincide_con_rutas_y_cabecera(client):
    contrato = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text())
    runtime = client.get("/openapi.json").json()
    post = "/v1/prestamos/{prestamo_id}/devolucion"
    alias = "/v1/prestamos/{prestamo_id}"
    assert contrato["paths"][alias]["delete"]["deprecated"] is runtime["paths"][alias]["delete"]["deprecated"] is True
    assert "post" in contrato["paths"][post] and "post" in runtime["paths"][post]
    parametro = next(p for p in runtime["paths"][post]["post"]["parameters"] if p["name"] == "Idempotency-Key")
    estatico = next(p for p in contrato["paths"][post]["post"]["parameters"] if p["name"] == "Idempotency-Key")
    assert parametro == estatico
    esquema = next(s for s in parametro["schema"]["anyOf"] if s["type"] == "string")
    assert esquema["maxLength"] == 64
    assert esquema["minLength"] == 1
    assert esquema["pattern"] == r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
