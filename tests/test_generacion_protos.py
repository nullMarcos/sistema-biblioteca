import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERADOR = ROOT / "scripts/generar_protos.py"


def test_cambio_de_contrato_y_deteccion_de_deriva(tmp_path):
    contrato = tmp_path / "catalogo.proto"
    contrato.write_text((ROOT / "contracts/catalogo.proto").read_text() + '\nmessage PruebaGeneracion { string evidencia = 1; }\n')
    salida = tmp_path / "protos"
    comando = [sys.executable, str(GENERADOR), "--contract", str(contrato), "--output", str(salida)]
    subprocess.run(comando, check=True)
    subprocess.run([sys.executable, "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import catalogo_pb2 as p; "
        "assert p.PruebaGeneracion(evidencia='contrato real').evidencia == 'contrato real'", str(salida)], check=True)
    subprocess.run(comando + ["--check"], check=True)
    archivo = salida / "catalogo_pb2.py"
    archivo.write_text(archivo.read_text() + "\n# deriva deliberada para la prueba\n")
    assert subprocess.run(comando + ["--check"], capture_output=True).returncode == 1
