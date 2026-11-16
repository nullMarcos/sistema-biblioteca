"""Verifica un proyecto existente sin borrar recursos ni asumir puertos internos."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlsplit

from verificar_compose import compose, ROOT
from verificar_operaciones import api


def verificar(proyecto, entorno, base_url, registrar=print):
    url = urlsplit(base_url)
    if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("El ensayo TCP requiere Gateway HTTP local (localhost o 127.0.0.1)")
    puerto = url.port or 80
    fallos = []

    def comprobar(nombre, condicion, detalle=None):
        registrar(f"{'PASS' if condicion else 'FAIL'}: {nombre}" + (f" ({detalle})" if detalle is not None else ""))
        if not condicion:
            fallos.append(nombre)

    ids = compose(proyecto, entorno, "ps", "--all", "--quiet", captura=True).split()
    if not ids:
        raise AssertionError(f"No hay contenedores en el proyecto {proyecto}")
    contenedores = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
    servicios = {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in contenedores}
    comprobar("cinco servicios del proyecto", set(servicios) == {"gateway", "prestamos", "sanciones", "catalogo", "bff"})
    gateway = servicios["gateway"]
    config = dict(e.split("=", 1) for e in gateway["Config"]["Env"] if "=" in e)
    limite, ventana = int(config.get("RATE_LIMIT", "20")), float(config.get("RATE_WINDOW", "60"))
    for servicio, contenedor in servicios.items():
        bindings = {p: b for p, b in (contenedor["NetworkSettings"]["Ports"] or {}).items() if b}
        configurados = {p: b for p, b in (contenedor["HostConfig"].get("PortBindings") or {}).items() if b}
        if servicio == "gateway":
            comprobar("binding de Gateway", set(bindings) == {"8080/tcp"}
                and {b["HostPort"] for b in bindings.get("8080/tcp", [])} == {str(puerto)})
        else:
            comprobar(f"{servicio} sin binding efectivo/configurado", not bindings and not configurados, bindings)
    # Un listener ajeno en :8000 no demuestra exposición de este proyecto. Las sondas
    # se registran separadamente y usan TCP, nunca HTTP sobre gRPC.
    sondas = {}
    for p in (puerto, 8000, 8001, 50051):
        try:
            with socket.create_connection((url.hostname, p), timeout=0.3):
                sondas[str(p)] = "abierto"
        except OSError:
            sondas[str(p)] = "cerrado"
    comprobar("TCP del binding público", sondas[str(puerto)] == "abierto")
    registrar("TCP host (listeners ajenos no se atribuyen al proyecto): " + json.dumps(sondas))

    def esperar_ventana():
        time.sleep(ventana + 0.15)

    esperar_ventana()
    casos = [("health", api(puerto, "/health")[0], 200),
        ("sin JWT", api(puerto, "/api/v1/socios")[0], 401),
        ("BFF sin JWT", api(puerto, "/bff/web/resumen-prestamos-socio/1")[0], 401),
        ("JWT inválido", api(puerto, "/api/v1/socios", "abc.def.ghi")[0], 401),
        ("API Key no sustituye JWT", api(puerto, "/api/v1/socios", headers_extra={"X-API-Key": "clave-enviada-por-cliente"})[0], 401),
        ("login inválido", api(puerto, "/auth/login", cuerpo={"username": "admin", "password": "mala"})[0], 401)]
    for nombre, obtenido, esperado in casos:
        comprobar(nombre, obtenido == esperado, obtenido)
    codigo, login = api(puerto, "/auth/login", cuerpo={"username": "admin", "password": "admin123"})
    comprobar("login válido", codigo == 200 and bool(login.get("access_token")))
    if codigo != 200 or not login.get("access_token"):
        raise AssertionError("No se pudo autenticar el ensayo")
    token = login["access_token"]
    comprobar("lectura autenticada", api(puerto, "/api/v1/socios", token)[0] == 200)
    comprobar("recurso inexistente", api(puerto, "/api/v1/xyz", token)[0] == 404)
    esperar_ventana()
    inicio = time.monotonic()
    respuestas = Counter(api(puerto, "/api/v1/socios" if i < limite else "/bff/web/resumen-prestamos-socio/1", token)[0]
        for i in range(limite + 5))
    duracion = time.monotonic() - inicio
    comprobar("ráfaga dentro de una ventana", duracion < ventana, round(duracion, 3))
    comprobar("rate limit compartido efectivo API/BFF", respuestas == Counter({200: limite, 429: 5}), dict(respuestas))
    if fallos:
        raise AssertionError(f"{len(fallos)} verificaciones fallaron: {', '.join(fallos)}")
    return {"proyecto": proyecto, "puerto": puerto, "rate_limit": limite, "rate_window_s": ventana,
        "respuestas_rafaga": dict(respuestas), "duracion_rafaga_ms": round(duracion * 1000, 3), "tcp": sondas}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proyecto", default=os.getenv("COMPOSE_PROJECT_NAME", ROOT.name))
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", f"http://127.0.0.1:{os.getenv('GATEWAY_PORT', '8080')}"))
    parser.add_argument("--reporte", type=Path)
    args = parser.parse_args()
    lineas = []

    def registrar(linea):
        print(linea, flush=True)
        lineas.append(linea)

    try:
        verificar(args.proyecto, os.environ.copy(), args.base_url, registrar)
    finally:
        if args.reporte:
            args.reporte.write_text("\n".join(lineas) + "\n")


if __name__ == "__main__":
    main()
