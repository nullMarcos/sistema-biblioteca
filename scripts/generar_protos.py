"""Generar o comprobar los stubs desde un solo contrato y compilador fijado."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVOS = ("catalogo_pb2.py", "catalogo_pb2_grpc.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=ROOT / "contracts/catalogo.proto")
    parser.add_argument("--output", type=Path, action="append")
    parser.add_argument("--check", action="store_true", help="Fallar ante deriva sin modificar stubs")
    args = parser.parse_args()
    contrato = args.contract.resolve(strict=True)
    destinos = args.output or [ROOT / f"{s}_service/protos" for s in ("catalogo", "prestamos")]
    with tempfile.TemporaryDirectory(prefix="biblioteca-protos-") as temporal:
        subprocess.run([
            sys.executable, "-m", "grpc_tools.protoc", f"-I{contrato.parent}",
            f"--python_out={temporal}", f"--grpc_python_out={temporal}", contrato.name,
        ], check=True, cwd=contrato.parent)
        diferencias = []
        for destino in destinos:
            for nombre in ARCHIVOS:
                contenido = (Path(temporal) / nombre).read_bytes()
                archivo = destino / nombre
                if args.check:
                    if not archivo.exists() or archivo.read_bytes() != contenido:
                        diferencias.append(str(archivo))
                else:
                    destino.mkdir(parents=True, exist_ok=True)
                    archivo.write_bytes(contenido)
        if diferencias:
            print("Stubs desactualizados:\n" + "\n".join(diferencias), file=sys.stderr)
            return 1
    print("Stubs comprobados." if args.check else "Stubs generados desde " + str(contrato))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
