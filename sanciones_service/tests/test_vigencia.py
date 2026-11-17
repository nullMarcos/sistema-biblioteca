from datetime import datetime, timedelta, timezone

import pytest

import main
from src.models import Bloqueo, Socio


AHORA = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("inicio,fin,estado,vigente", [
    (-2, -1, "ACTIVO", False),
    (1, 2, "ACTIVO", False),
    (-1, 0, "ACTIVO", False),
    (0, 1, "ACTIVO", True),
    (-1, None, "ACTIVO", True),
    (-1, 1, "ACTIVO", True),
    (-1, 1, "LEVANTADO", False),
])
@pytest.mark.parametrize("listado", [False, True])
def test_solo_bloquea_durante_su_vigencia_sin_modificar_historial(client, db, monkeypatch, inicio, fin, estado, vigente, listado):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA

    monkeypatch.setattr(main, "datetime", Reloj, raising=False)
    socio = Socio(rut="44.444.444-4", nombre="Vigencia", correo="vigencia@example.com")
    socio.bloqueos = [Bloqueo(motivo="Prueba de vigencia", estado=estado,
        fecha_inicio=AHORA + timedelta(hours=inicio),
        fecha_fin=AHORA + timedelta(hours=fin) if fin is not None else None)]
    db.add(socio)
    db.commit()
    db.expire_all()  # SQLite devuelve fechas naive; el contrato las interpreta como UTC.
    bloqueo = db.query(Bloqueo).one()
    original = (bloqueo.estado, bloqueo.fecha_inicio, bloqueo.fecha_fin)
    ruta = "/v1/sanciones/socios" + ("" if listado else f"/{socio.rut}")
    respuesta = client.get(ruta)
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()[0] if listado else respuesta.json()
    assert datos["bloqueado"] is vigente
    assert datos["tiene_sancion"] is vigente
    assert len(datos["bloqueos_activos"]) == int(vigente)
    db.expire_all()
    assert (bloqueo.estado, bloqueo.fecha_inicio, bloqueo.fecha_fin) == original


@pytest.mark.parametrize("identificador,status", [(str(2 ** 63), 400), ("²", 404)])
def test_identificador_no_convertible_a_sqlite_no_es_error_interno(client, identificador, status):
    respuesta = client.get(f"/v1/sanciones/socios/{identificador}")
    assert respuesta.status_code == status, respuesta.text
