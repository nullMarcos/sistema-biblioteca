import httpx
import pytest
import time
import jwt

import main


def test_errores_de_validacion_y_autenticacion_tienen_contrato(client):
    for respuesta, codigo in ((client.post("/auth/login", json={}), 400),
            (client.post("/auth/login", json={"username": "no", "password": "no"}), 401),
            (client.get("/api/v1/socios"), 401), (client.get("/no-existe"), 404)):
        assert respuesta.status_code == codigo, respuesta.text
        assert set(respuesta.json()) == {"codigo", "mensaje"}


@pytest.mark.parametrize("error,codigo", [(httpx.ReadTimeout("espera agotada"), 504),
    (httpx.ConnectError("sin servidor"), 503), (httpx.RemoteProtocolError("respuesta inválida"), 502)])
def test_proxy_distingue_espera_disponibilidad_y_protocolo(client, monkeypatch, error, codigo):
    async def fallar(*args, **kwargs):
        raise error

    monkeypatch.setattr(main.client, "request", fallar)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    respuesta = client.get("/api/v1/socios", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == codigo, respuesta.text
    assert set(respuesta.json()) == {"codigo", "mensaje"}


def test_rate_limit_conserva_retry_after_y_error_estandar(client, monkeypatch):
    monkeypatch.setattr(main, "LIMITE_PETICIONES", 1)
    assert client.post("/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    respuesta = client.get("/api/v1/socios")
    assert respuesta.status_code == 429
    assert int(respuesta.headers["Retry-After"]) > 0
    assert set(respuesta.json()) == {"codigo", "mensaje"}


@pytest.mark.parametrize("cambio", [{"role": None}, {"role": []}, {"role": "desconocido"}, {"sub": ""}, {"exp": 0}])
def test_claims_incompletos_o_invalidos_no_producen_500(client, cambio):
    claims = {"sub": "1", "role": "admin", "iat": int(time.time()), "exp": int(time.time()) + 60, **cambio}
    token = jwt.encode(claims, main.JWT_SECRET, algorithm=main.JWT_ALGORITMO)
    respuesta = client.get("/api/v1/socios", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 401, respuesta.text
    assert set(respuesta.json()) == {"codigo", "mensaje"}


def test_error_inesperado_conserva_500_con_error_estandar(client, monkeypatch):
    async def fallar(*args, **kwargs):
        raise RuntimeError("Fallo local inesperado")

    monkeypatch.setattr(main.client, "request", fallar)
    token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    respuesta = client.get("/api/v1/socios", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 500
    assert respuesta.json() == {"codigo": "ERROR_INTERNO", "mensaje": "Error interno del servicio"}
