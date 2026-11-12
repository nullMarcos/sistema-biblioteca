"""
Constantes de estado y pasos para el orquestador de Saga.
"""

from enum import Enum


class SagaState(str, Enum):
    """Estados del ciclo de vida de una transacción distribuida Saga."""
    INICIADA = "INICIADA"
    EJEMPLAR_RESERVADO = "EJEMPLAR_RESERVADO"
    SANCIONES_VERIFICADAS = "SANCIONES_VERIFICADAS"
    COMPLETADA = "COMPLETADA"
    COMPENSANDO = "COMPENSANDO"
    COMPENSADA = "COMPENSADA"
    FALLIDA = "FALLIDA"


class SagaStep(str, Enum):
    """Identificadores de pasos de la Saga de préstamo."""
    INICIO = "INICIO"
    PASO_1_RESERVA_CATALOGO = "RESERVAR_EJEMPLAR_CATALOGO"
    PASO_2_VERIFICAR_SANCIONES = "VERIFICAR_SANCIONES_SOCIO"
    PASO_3_REGISTRAR_PRESTAMO = "REGISTRAR_PRESTAMO_LOCAL"
