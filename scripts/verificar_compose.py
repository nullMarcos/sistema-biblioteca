"""C03: dos proyectos desechables, aislamiento real y arranque sin Sanciones.

Solo crea/elimina recursos con los nombres aleatorios generados por esta ejecución.
Los servicios y volúmenes del usuario no se usan como datos de prueba.
"""

import json
import os
import socket
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def puerto_libre():
    with socket.socket() as conexion:
        conexion.bind(("127.0.0.1", 0))
        return conexion.getsockname()[1]


def compose(proyecto, entorno, *args, captura=False):
    return subprocess.run(["docker", "compose", "--env-file", os.devnull,
        "-f", str(ROOT / "docker-compose.yml"), "-p", proyecto, *args], cwd=ROOT,
        env=entorno, check=True, capture_output=captura, text=True).stdout


def http(puerto, ruta, token=None, cuerpo=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", headers=headers,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as respuesta:
        return json.load(respuesta)


def inspeccionar(proyecto, entorno, puerto):
    ids = compose(proyecto, entorno, "ps", "--all", "--quiet", captura=True).split()
    assert len(ids) == 5, f"{proyecto}: se esperaban cinco contenedores"
    contenedores = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
    redes, volumenes = set(), set()
    for contenedor in contenedores:
        servicio = contenedor["Config"]["Labels"]["com.docker.compose.service"]
        assert contenedor["State"]["Health"]["Status"] == "healthy", servicio
        bindings = {p: v for p, v in (contenedor["NetworkSettings"]["Ports"] or {}).items() if v}
        if servicio == "gateway":
            assert set(bindings) == {"8080/tcp"}, bindings
            assert {b["HostPort"] for b in bindings["8080/tcp"]} == {str(puerto)}
        else:
            assert not bindings, f"Puerto interno publicado: {servicio}: {bindings}"
        if servicio == "bff":
            assert not contenedor["Mounts"], "BFF debe carecer de persistencia y montajes de otras bases"
        redes.update(contenedor["NetworkSettings"]["Networks"])
        volumenes.update(m["Name"] for m in contenedor["Mounts"] if m["Type"] == "volume")
    assert len(redes) == 1 and len(volumenes) == 3
    with socket.create_connection(("127.0.0.1", puerto), timeout=2):
        pass
    compose(proyecto, entorno, "exec", "-T", "prestamos", "python", "-c",
        "import socket, sys, urllib.request\n"
        "[socket.getaddrinfo(s, None) for s in ('catalogo','prestamos','sanciones','bff','gateway')]\n"
        "urllib.request.urlopen('http://bff:8000/health', timeout=3).close()\n"
        "urllib.request.urlopen('http://sanciones:8000/health', timeout=3).close()\n"
        "sys.path.insert(0, '/app/protos')\n"
        "import grpc, catalogo_pb2 as pb, catalogo_pb2_grpc as rpc\n"
        "with grpc.insecure_channel('catalogo:50051') as canal:\n"
        "    assert rpc.CatalogoStub(canal).ListarCatalogo(pb.CatalogoRequest(), timeout=3).libros\n")
    return redes, volumenes


def main():
    identificador = uuid.uuid4().hex[:10]
    proyectos = []
    try:
        for sufijo in ("a", "b"):
            nombre = f"bibg01{identificador}{sufijo}"
            puerto = puerto_libre()
            entorno = {**os.environ, "GATEWAY_PORT": str(puerto),
                "API_KEY": "clave-compose-desechable", "JWT_SECRET": "secreto-compose-desechable-con-longitud-suficiente",
                "SANCIONES_URL": "http://sanciones:8000"}
            proyectos.append((nombre, entorno, puerto))
            compose(nombre, entorno, "up", "-d", "--build", "--wait", "--wait-timeout", "120")
        recursos = [inspeccionar(*p) for p in proyectos]
        assert recursos[0][0].isdisjoint(recursos[1][0]), "Red compartida entre proyectos"
        assert recursos[0][1].isdisjoint(recursos[1][1]), "Volumen compartido entre proyectos"
        print("PASS: dos proyectos saludables; redes/volúmenes propios y solo Gateway publicado.", flush=True)

        tokens = [http(p, "/auth/login", cuerpo={"username": "admin", "password": "admin123"})["access_token"]
            for _, _, p in proyectos]
        rut = "44.444.444-4"
        http(proyectos[0][2], "/api/v1/socios", tokens[0], {"rut": rut, "nombre": "Prueba C03", "correo": "c03@example.com"})
        assert len(http(proyectos[0][2], f"/api/v1/socios?rut={rut}", tokens[0])) == 1
        assert http(proyectos[1][2], f"/api/v1/socios?rut={rut}", tokens[1]) == []
        print("PASS: una escritura en el primer proyecto no aparece en el segundo.", flush=True)

        nombre, entorno, puerto = proyectos[0]
        compose(nombre, entorno, "stop", "sanciones")
        compose(nombre, entorno, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", "prestamos")
        assert len(http(puerto, f"/api/v1/socios?rut={rut}", tokens[0])) == 1
        print("PASS: Préstamos arranca sin Sanciones y conserva lecturas y datos.", flush=True)
    finally:
        errores = []
        for nombre, entorno, _ in reversed(proyectos):
            try:
                compose(nombre, entorno, "down", "--volumes", "--remove-orphans", "--rmi", "local")
            except subprocess.CalledProcessError as error:
                print(f"No se pudo completar la limpieza de {nombre}.", file=sys.stderr)
                errores.append(error)
        if errores:
            raise RuntimeError("La limpieza de los proyectos desechables falló.") from errores[0]


if __name__ == "__main__":
    main()
