"""Procesos reales de prueba con puertos y archivos SQLite desechables."""

from contextlib import contextmanager
from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import urllib.request

import grpc

RAIZ = Path(__file__).resolve().parents[2]


def puerto_libre():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def esperar(condicion, proceso=None, timeout=8):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return
        if proceso and proceso.poll() is not None:
            raise AssertionError(f"El proceso terminó con {proceso.returncode} antes del punto esperado")
        time.sleep(0.02)
    raise AssertionError("No se alcanzó el punto esperado en el proceso de prueba")


@contextmanager
def proceso(args, servicio, env, log):
    with log.open("ab") as salida:
        proc = subprocess.Popen([sys.executable, *args], cwd=RAIZ / servicio,
            env={**os.environ, **env}, stdout=salida, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)
        try:
            yield proc
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


@contextmanager
def catalogo(base, log, puerto=None):
    puerto = puerto or puerto_libre()
    with proceso(["main.py"], "catalogo_service", {"DATABASE_URL": f"sqlite:///{base}", "GRPC_PORT": str(puerto)}, log):
        canal = grpc.insecure_channel(f"127.0.0.1:{puerto}")
        try:
            grpc.channel_ready_future(canal).result(timeout=8)
            yield puerto
        finally:
            canal.close()


def http_listo(url):
    try:
        with urllib.request.urlopen(url, timeout=0.2) as respuesta:
            return respuesta.status == 200
    except (OSError, urllib.error.URLError):
        return False
