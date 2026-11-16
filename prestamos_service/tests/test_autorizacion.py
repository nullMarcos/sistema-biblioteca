from datetime import date

import pytest

from src import models


@pytest.fixture
def otros_socios(db):
    db.add_all([models.Socio(id=1, rut="11.111.111-1", nombre="Titular", correo="titular@example.com"),
        models.Socio(id=2, rut="22.222.222-2", nombre="Otro socio", correo="otro@example.com")])
    db.add_all([models.Prestamo(id=1, socio_id=1, libro_id=1, ejemplar_id=1, fecha_limite=date.today(), estado="ACTIVO"),
        models.Prestamo(id=2, socio_id=2, libro_id=2, ejemplar_id=2, fecha_limite=date.today(), estado="ACTIVO")])
    db.add(models.SagaTransaccion(saga_id="saga-otro", idempotency_key="clave-otro", socio_id=2, libro_id=2,
        estado="COMPLETADA", paso_actual="FIN", prestamo_id=2))
    db.add(models.DevolucionOperacion(operacion_id="dev-otro", idempotency_key="devolver-otro", prestamo_id=2,
        reserva_id="reserva-otro", liberacion_id="liberar-otro", estado_final="DEVUELTO", estado="PENDIENTE"))
    db.commit()
    return {"X-User-Id": "cuenta-2", "X-User-Role": "socio", "X-Socio-Id": "1"}


@pytest.mark.parametrize("ruta", ["/v1/socios/2", "/v1/prestamos/2", "/v1/prestamos/saga/saga-otro",
    "/v1/prestamos/saga/idempotency/clave-otro", "/v1/prestamos/devoluciones/idempotency/devolver-otro"])
def test_socio_no_puede_leer_recursos_ajenos_por_rutas_alternativas(client, otros_socios, ruta):
    respuesta = client.get(ruta, headers=otros_socios)
    assert respuesta.status_code == 403, respuesta.text
    assert set(respuesta.json()) == {"codigo", "mensaje"}


def test_listados_se_filtran_en_el_servicio_por_socio_autenticado(client, otros_socios):
    assert [s["id"] for s in client.get("/v1/socios", headers=otros_socios).json()] == [1]
    assert client.get("/v1/socios?rut=22.222.222-2", headers=otros_socios).json() == []
    assert [p["socio_id"] for p in client.get("/v1/prestamos", headers=otros_socios).json()] == [1]
    assert client.get("/v1/socios/1", headers=otros_socios).status_code == 200


def test_socio_no_crea_socios_ni_solicita_para_otro(client, db, otros_socios):
    assert client.post("/v1/socios", headers=otros_socios,
        json={"rut": "33.333.333-3", "nombre": "Alta ajena", "correo": "alta@example.com"}).status_code == 403
    assert client.post("/v1/prestamos", headers=otros_socios, json={"socio_id": 2, "libro_id": 1}).status_code == 403
    assert db.query(models.Socio).count() == 2 and db.query(models.SagaTransaccion).count() == 1


@pytest.mark.parametrize("metodo,ruta", [("post", "/v1/prestamos/2/devolucion"), ("delete", "/v1/prestamos/2")])
def test_socio_no_devuelve_prestamos_ajenos(client, otros_socios, metodo, ruta):
    respuesta = getattr(client, metodo)(ruta, headers=otros_socios)
    assert respuesta.status_code == 403, respuesta.text


def test_identidad_incompleta_no_se_interpreta_como_operador_interno(client):
    assert client.get("/v1/socios", headers={"X-User-Role": "socio", "X-User-Id": "2"}).status_code == 401
    assert client.get("/v1/socios", headers={"X-Socio-Id": "1"}).status_code == 401
