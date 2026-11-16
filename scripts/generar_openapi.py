"""Exporta contratos REST desde FastAPI en procesos aislados; --check detecta deriva."""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def contratos():
    documentos = {}
    with tempfile.TemporaryDirectory(prefix="biblioteca-openapi-") as temporal:
        for servicio in ("prestamos", "sanciones", "gateway", "bff"):
            codigo = "import json, main; print(json.dumps(main.app.openapi()))"
            if servicio == "prestamos":
                codigo += "; main.grpc_client.cerrar_canal()"
            if servicio == "gateway":
                codigo += "; assert main.client is None"
            env = {**os.environ, "DATABASE_URL": f"sqlite:///{temporal}/{servicio}.db", "API_KEY": "solo-exportacion-contrato",
                "JWT_SECRET": "solo-exportacion-contrato-sin-tokens-reales"}
            salida = subprocess.run([sys.executable, "-c", codigo], cwd=ROOT / f"{servicio}_service",
                env=env, check=True, capture_output=True, text=True)
            documento = json.loads(salida.stdout)
            documento["servers"] = [{"url": f"http://{servicio}:{8080 if servicio == 'gateway' else 8000}",
                "description": "Entrada pública (usar el host/puerto de Gateway)" if servicio == "gateway" else "Solo red interna Compose"}]
            documentos[servicio] = documento

    prestamos, sanciones, gateway = (documentos[s] for s in ("prestamos", "sanciones", "gateway"))
    bff = copy.deepcopy(documentos["bff"])
    bff["servers"] = [{"url": "/", "description": "Entrada pública de Gateway; usar su host y puerto"}]
    bff["paths"] = {p: item for p, item in bff["paths"].items() if p.startswith("/bff/web/")}
    bff["components"]["securitySchemes"] = {"HTTPBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
    bff["info"]["description"] = "Contrato público web por Gateway. JWT y autorización por socio; API Key e identidad técnica son internas. EN_REVISION no autoriza entrega; desconocidos se representan como null."
    for ruta, item in bff["paths"].items():
        for operacion in item.values():
            operacion["security"] = [{"HTTPBearer": []}]
            operacion.setdefault("parameters", []).append({"name": "X-Request-Id", "in": "header", "required": False,
                "description": "Correlación separada de Idempotency-Key; se genera si falta o no es válida",
                "schema": {"type": "string", "maxLength": 64, "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"}})
            for estado, respuesta in operacion["responses"].items():
                cabeceras = respuesta.setdefault("headers", {})
                cabeceras["X-Request-Id"] = {"schema": {"type": "string"}, "description": "Correlación de esta petición"}
                if ruta.endswith("solicitar-prestamo"):
                    cabeceras["Idempotency-Key"] = {"schema": {"type": "string"}, "description": "Identidad estable de la escritura, cuando se recibió una clave válida"}
                if estado in {"429", "503"}:
                    cabeceras["Retry-After"] = {"schema": {"type": "string"}, "description": "Espera sugerida cuando corresponda"}
    prestamos["info"]["description"] += " Contrato público equivalente: gateway.openapi.yaml."
    for ruta, item in prestamos["paths"].items():
        if ruta.startswith("/v1/"):
            item["x-public-path"] = "/api" + ruta
    # El proxy acepta rutas dinámicas; el contrato público concreta las operaciones soportadas.
    publico = copy.deepcopy(gateway)
    publico["paths"] = {p: o for p, o in publico["paths"].items() if not p.startswith(("/api/v1/", "/bff/"))}
    publico["components"]["schemas"].update(copy.deepcopy(prestamos["components"]["schemas"]))
    for ruta, item in prestamos["paths"].items():
        if not ruta.startswith("/v1/"):
            continue
        nuevo = copy.deepcopy(item)
        nuevo.pop("x-public-path", None)
        nuevo["x-upstream-path"] = ruta
        for metodo, operacion in nuevo.items():
            if metodo.startswith("x-") or metodo == "parameters":
                continue
            operacion["security"] = [{"HTTPBearer": []}]
            operacion["responses"]["401"] = {"description": "JWT ausente, inválido o expirado",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}}
            for estado, descripcion in (("429", "Límite de peticiones; Retry-After indica la espera"),
                    ("502", "Respuesta o protocolo remoto inválido"), ("503", "Servicio no disponible"),
                    ("504", "Tiempo de espera agotado; una escritura puede requerir consulta por identidad")):
                operacion["responses"][estado] = {"description": descripcion,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}}
            operacion["operationId"] = "gateway_" + operacion["operationId"]
        publico["paths"]["/api" + ruta] = nuevo
    for nombre, esquema in bff["components"]["schemas"].items():
        if nombre in publico["components"]["schemas"] and publico["components"]["schemas"][nombre] != esquema:
            raise ValueError(f"Schemas públicos incompatibles con el mismo nombre: {nombre}")
        publico["components"]["schemas"][nombre] = esquema
    publico["paths"].update(copy.deepcopy(bff["paths"]))
    return {"openapi.yaml": prestamos, "sanciones.openapi.yaml": sanciones, "gateway.openapi.yaml": publico, "bff.openapi.yaml": bff}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for nombre, documento in contratos().items():
        ruta = ROOT / "contracts" / nombre
        if args.check:
            if not ruta.exists() or yaml.safe_load(ruta.read_text()) != documento:
                raise SystemExit(f"Deriva de contrato REST: {ruta.relative_to(ROOT)}; ejecutar scripts/generar_openapi.py")
        else:
            ruta.write_text(yaml.safe_dump(documento, allow_unicode=True, sort_keys=False, width=110))
        print(f"OK: {ruta.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
