#!/usr/bin/env python3
"""Entrada de demostración de resiliencia del consumidor real, con datos aislados.

El antiguo script importaba sanciones_service/src/client.py. Este comando ahora
verifica la Saga y prestamos_service/src/sanciones_client.py mediante Compose.
La implementación aislada del otro cliente se conserva en su servicio.
"""

import sys

from test_saga_escenarios import main


if __name__ == "__main__":
    main(["--grupo", "resiliencia", *sys.argv[1:]])
