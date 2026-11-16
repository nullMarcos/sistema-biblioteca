from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time

import grpc
import pytest
from sqlalchemy import text

import catalogo_pb2 as pb
from src import server


@pytest.mark.parametrize("repeticion", range(3))
def test_veinte_reservas_solo_asignan_dos_copias(stub, repeticion):
    inicio = Barrier(20)

    def reservar(i):
        inicio.wait(timeout=5)
        return stub.ReservarEjemplar(pb.ReservaRequest(libro_id=1, operacion_id=f"concurrente-{i}"), timeout=5)

    with ThreadPoolExecutor(max_workers=20) as pool:
        resultados = list(pool.map(reservar, range(20)))
    asignados = [r.ejemplar_id for r in resultados if r.exito]
    assert len(asignados) == len(set(asignados)) == 2
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 0


def test_disponibilidad_inexistente_es_not_found(stub):
    with pytest.raises(grpc.RpcError) as error:
        stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=987654), timeout=2)
    assert error.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize("identificador", [0, -1])
def test_rpc_rechazan_ids_no_positivos(stub, identificador):
    llamadas = [
        (stub.ConsultarDisponibilidad, pb.ConsultaRequest(libro_id=identificador)),
        (stub.ReservarEjemplar, pb.ReservaRequest(libro_id=identificador)),
        (stub.LiberarEjemplar, pb.LiberarRequest(ejemplar_id=identificador)),
        (stub.ObtenerLibro, pb.LibroRequest(libro_id=identificador)),
    ]
    for metodo, request in llamadas:
        with pytest.raises(grpc.RpcError) as error:
            metodo(request, timeout=2)
        assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_fallo_real_de_bd_no_modifica_inventario(stub):
    with server.SessionLocal() as db:
        db.execute(text("CREATE TRIGGER fallo_reserva BEFORE UPDATE ON ejemplares BEGIN SELECT RAISE(ABORT, 'fallo probado'); END"))
        db.commit()
    with pytest.raises(grpc.RpcError) as error:
        stub.ReservarEjemplar(pb.ReservaRequest(libro_id=1, operacion_id="fallo-bd"), timeout=2)
    assert error.value.code() == grpc.StatusCode.INTERNAL
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2


def test_busy_es_indisponibilidad_acotada_y_no_reserva(stub):
    with server.SessionLocal() as escritor:
        escritor.execute(text("BEGIN IMMEDIATE"))
        inicio = time.monotonic()
        with pytest.raises(grpc.RpcError) as error:
            stub.ReservarEjemplar(pb.ReservaRequest(libro_id=1, operacion_id="busy"), timeout=2)
        assert error.value.code() == grpc.StatusCode.UNAVAILABLE
        assert "SQLITE_BUSY" in error.value.details()
        assert time.monotonic() - inicio < 1
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2
