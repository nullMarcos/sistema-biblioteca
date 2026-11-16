import grpc
import pytest
import catalogo_pb2 as pb


def test_lectura_y_serializacion_reales(stub):
    libro = stub.ObtenerLibro(pb.LibroRequest(libro_id=1), timeout=2)
    assert (libro.id, libro.titulo, libro.autor) == (1, "Dune", "Frank Herbert")
    assert libro.total_ejemplares == libro.ejemplares_disponibles == 2
    assert {e.id for e in libro.ejemplares} == {1, 2}


def test_obtener_libro_inexistente(stub):
    with pytest.raises(grpc.RpcError) as error:
        stub.ObtenerLibro(pb.LibroRequest(libro_id=987654), timeout=2)
    assert error.value.code() == grpc.StatusCode.NOT_FOUND


def test_reserva_y_liberacion_secuenciales(stub):
    reserva = stub.ReservarEjemplar(pb.ReservaRequest(libro_id=1), timeout=2)
    assert reserva.exito
    assert reserva.ejemplar_id in {1, 2}
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 1
    assert stub.LiberarEjemplar(pb.LiberarRequest(ejemplar_id=reserva.ejemplar_id), timeout=2).exito
    assert stub.ConsultarDisponibilidad(pb.ConsultaRequest(libro_id=1), timeout=2).ejemplares_disponibles == 2
