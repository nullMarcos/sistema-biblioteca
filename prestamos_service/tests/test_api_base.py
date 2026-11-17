from datetime import date, datetime, timezone

import pytest

from src import models


def test_rechaza_correo_invalido(client):
    response = client.post("/v1/socios", json={"rut": "11.111.111-1", "nombre": "Ana", "correo": "invalido"})
    assert response.status_code == 400
    assert response.json()["codigo"] == "ERROR_VALIDACION"


def test_socio_inexistente(client):
    response = client.get("/v1/socios/987654")
    assert response.status_code == 404
    assert response.json() == {"codigo": "SOCIO_NO_ENCONTRADO", "mensaje": "No existe un Socio con ese ID"}


@pytest.mark.parametrize("metodo,ruta,cuerpo", [
    ("get", f"/v1/socios/{2 ** 63}", None),
    ("get", f"/v1/prestamos/{2 ** 63}", None),
    ("post", f"/v1/prestamos/{2 ** 63}/devolucion", {}),
    ("delete", f"/v1/prestamos/{2 ** 63}", None),
    ("post", "/v1/prestamos", {"socio_id": 2 ** 63, "libro_id": 1}),
])
def test_id_fuera_del_rango_sqlite_es_validacion_sin_saga(client, db, metodo, ruta, cuerpo):
    respuesta = client.request(metodo, ruta, json=cuerpo)
    assert respuesta.status_code == 400, respuesta.text
    assert respuesta.json()["codigo"] == "ERROR_VALIDACION"
    assert db.query(models.SagaTransaccion).count() == 0


def test_prestamo_sin_socio_no_reserva(client):
    response = client.post("/v1/prestamos", json={"socio_id": 987654, "libro_id": 1})
    assert response.status_code == 404
    assert response.json()["codigo"] == "SOCIO_NO_ENCONTRADO"


def test_crear_y_consultar_socio(client):
    payload = {"rut": "11.111.111-1", "nombre": "Ana", "correo": "ana@example.com"}
    response = client.post("/v1/socios", json=payload)
    assert response.status_code == 201
    creado = response.json()
    assert creado == {**payload, "id": creado["id"], "prestamos": []}
    assert client.get(f"/v1/socios/{creado['id']}").json() == creado


def test_serializacion_prestamo_y_enlaces(client, db):
    socio = models.Socio(rut="11.111.111-1", nombre="Ana", correo="ana@example.com")
    db.add(socio)
    db.flush()
    prestamo = models.Prestamo(socio_id=socio.id, libro_id=1, ejemplar_id=3,
        fecha_prestamo=datetime(2026, 11, 16, tzinfo=timezone.utc), fecha_limite=date(2026, 11, 30), estado="ACTIVO")
    db.add(prestamo)
    db.commit()
    response = client.get(f"/v1/prestamos/{prestamo.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["ejemplar_id"] == 3
    assert data["fecha_limite"] == "2026-11-30"
    assert data["fecha_devolucion"] is None
    assert data["_links"]["self"] == {"href": f"/v1/prestamos/{prestamo.id}", "method": "GET"}
    assert data["_links"]["socio"]["href"] == f"/v1/socios/{socio.id}"
