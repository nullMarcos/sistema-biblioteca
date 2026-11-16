import grpc
import pytest

from src import grpc_client as cliente
import catalogo_pb2 as pb


def test_reintento_usa_misma_identidad_tras_respuesta_perdida(monkeypatch):
    llamadas = []

    class Perdida(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.DEADLINE_EXCEEDED

        def details(self):
            return "El servidor confirmó; se perdió la respuesta"

    def reservar(request, **kwargs):
        llamadas.append(request)
        if len(llamadas) == 1:
            raise Perdida()
        return pb.ReservaResponse(exito=True, ejemplar_id=1, estado="RESERVADA")

    monkeypatch.setattr(cliente.stub, "ReservarEjemplar", reservar)
    monkeypatch.setattr(cliente, "INITIAL_BACKOFF_SEGS", 0)
    assert cliente.reservar_ejemplar(1, "operacion-persistida") == 1
    assert len(llamadas) == 2
    assert llamadas[0] == llamadas[1]
    assert llamadas[0].operacion_id == "operacion-persistida"


def test_reserva_liberada_no_se_interpreta_como_nueva_asignacion(monkeypatch):
    monkeypatch.setattr(cliente.stub, "ReservarEjemplar", lambda *args, **kwargs:
        pb.ReservaResponse(exito=True, ejemplar_id=1, estado="LIBERADA"))
    with pytest.raises(cliente.CatalogoConflictoError):
        cliente.reservar_ejemplar(1, "operacion-terminada")


@pytest.mark.parametrize("estado,ejemplar", [("", 1), ("RESERVADA", 0), ("DESCONOCIDA", 1)])
def test_confirmacion_invalida_no_se_consolida_como_reserva(monkeypatch, estado, ejemplar):
    monkeypatch.setattr(cliente.stub, "ReservarEjemplar", lambda *args, **kwargs:
        pb.ReservaResponse(exito=True, ejemplar_id=ejemplar, estado=estado))
    with pytest.raises(cliente.CatalogoError) as error:
        cliente.reservar_ejemplar(1, "confirmacion-invalida")
    assert error.value.codigo == "RESPUESTA_CATALOGO_INVALIDA"


@pytest.mark.parametrize("cancelar", [False, True])
def test_ack_sin_estado_no_confirma_liberacion_o_cancelacion(monkeypatch, cancelar):
    monkeypatch.setattr(cliente.stub, "CancelarReserva" if cancelar else "LiberarEjemplar",
        lambda *args, **kwargs: pb.LiberarResponse(exito=True))
    with pytest.raises(cliente.CatalogoError) as error:
        if cancelar:
            cliente.cancelar_reserva(1, "reserva", "cancelacion")
        else:
            cliente.liberar_ejemplar(1, "liberacion", "reserva")
    assert error.value.codigo == "RESPUESTA_CATALOGO_INVALIDA"
