"""Ejecutar suites en procesos separados: cada servicio tiene su propio paquete src."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts/generar_protos.py"), "--check"], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/generar_openapi.py"), "--check"], check=True)
    for carpeta in (ROOT, ROOT / "catalogo_service", ROOT / "prestamos_service", ROOT / "sanciones_service", ROOT / "gateway_service", ROOT / "bff_service"):
        subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=carpeta, check=True)


if __name__ == "__main__":
    main()
