import json
import time

import httpx
import jwt
import pytest

import main


@pytest.mark.parametrize("socio_id", [None, 0, -1, "1", True])
def test_jwt_socio_exige_vinculo_positivo_y_tipado(client, socio_id):
    claims = {"sub": "2", "role": "socio", "iat": int(time.time()), "exp": int(time.time()) + 60}
    if socio_id is not None:
        claims["socio_id"] = socio_id
    token = jwt.encode(claims, main.JWT_SECRET, algorithm=main.JWT_ALGORITMO)
    assert client.get("/api/v1/socios", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("campo,valor", [("exp", 10 ** 400), ("iat", -(10 ** 400))])
def test_jwt_fecha_fuera_de_rango_es_401_sin_llamada_interna(client, monkeypatch, campo, valor):
    llamadas = []
    async def remoto(*args, **kwargs):
        llamadas.append(1)
        return httpx.Response(200, json={})
    monkeypatch.setattr(main.client, "request", remoto)
    claims = {"sub": "1", "role": "admin", "iat": int(time.time()), "exp": int(time.time()) + 60, campo: valor}
    token = jwt.encode(claims, main.JWT_SECRET, algorithm=main.JWT_ALGORITMO)
    respuesta = client.get("/bff/web/resumen-prestamos-socio/1", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 401, respuesta.text
    assert not llamadas


def test_gateway_bff_reemplaza_toda_identidad_y_propaga_respuesta_y_clave(client, monkeypatch):
    observadas = []
    async def remoto(metodo, destino, **kwargs):
        observadas.append((metodo, destino, kwargs))
        return httpx.Response(201, json={"estado": "EN_REVISION"}, headers={"Retry-After": "2", "Idempotency-Key": "externa"})
    monkeypatch.setattr(main.client, "request", remoto)
    token = client.post("/auth/login", json={"username": "socio1", "password": "socio123"}).json()["access_token"]
    claims = jwt.decode(token, main.JWT_SECRET, algorithms=[main.JWT_ALGORITMO])
    assert claims["sub"] != str(claims["socio_id"])
    respuesta = client.post("/bff/web/solicitar-prestamo?opcion=valor", json={"socio_id": 1, "libro_id": 1},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "externa", "X-Request-Id": "correlacion",
            "X-API-Key": "falsa", "X-User-Role": "admin", "X-User-Id": "otra-cuenta", "X-User-Otro": "infiltrado", "X-Socio-Id": "999"})
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.headers["Retry-After"] == "2" and respuesta.headers["Idempotency-Key"] == "externa"
    assert respuesta.headers["X-Request-Id"] == "correlacion" and len(observadas) == 1
    metodo, destino, parametros = observadas[0]
    assert metodo == "POST" and destino.endswith("/bff/web/solicitar-prestamo") and "opcion=valor" in str(parametros["params"])
    headers = {k.lower(): v for k, v in parametros["headers"].items()}
    assert headers["x-user-id"] == claims["sub"] and headers["x-user-role"] == "socio"
    assert headers["x-socio-id"] == str(claims["socio_id"]) and "x-user-otro" not in headers
    assert "authorization" not in headers and headers["x-api-key"] != "falsa"
    assert json.loads(parametros["content"]) == {"socio_id": 1, "libro_id": 1}


def test_ruta_bff_comparte_autenticacion_y_rate_limit(client, monkeypatch):
    assert client.get("/bff/web/resumen-prestamos-socio/1").status_code == 401
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    async def remoto(*args, **kwargs):
        return httpx.Response(200, json={"parcial": False})
    monkeypatch.setattr(main.client, "request", remoto)
    main.peticiones_por_ip.clear()
    monkeypatch.setattr(main, "LIMITE_PETICIONES", 1)
    assert client.get("/bff/web/resumen-prestamos-socio/1", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    respuesta = client.get("/api/v1/socios", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 429 and "Retry-After" in respuesta.headers


@pytest.mark.parametrize("ruta", ["/bff/web/%2E%2E/%2E%2E/internal/metricas",
    "/api/v1/socios/%2E%2E/%2E%2E/internal/resiliencia"])
def test_proxy_no_permite_escapar_del_prefijo_publico(client, monkeypatch, ruta):
    destinos = []
    async def remoto(metodo, destino, **kwargs):
        destinos.append(str(httpx.URL(destino)))
        return httpx.Response(200, json={"diagnostico": "interno"})
    monkeypatch.setattr(main.client, "request", remoto)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    respuesta = client.get(ruta, headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 404, (respuesta.text, destinos)
    assert not destinos
