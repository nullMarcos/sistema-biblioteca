#!/usr/bin/env python3
"""Compara el tamaño serializado de LibroDTO en JSON y Protocol Buffers.

Genera los CSV y el gráfico en esta carpeta. Ejecutar desde la raíz con
`python docs/experimentos/experimento.py`.
"""

from __future__ import annotations

import csv
import gzip
import json
import platform
import statistics
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent
PROTO_DIR = ROOT_DIR / "catalogo_service" / "protos"

SIZES = (1, 5, 10, 25, 50, 100, 250, 500)
REPETITIONS = 30
GZIP_LEVEL = 9
BOOK_ID = 4201
BOOK_TITLE = "Cien años de soledad — edición de aniversario"
BOOK_AUTHOR = "Gabriel García Márquez"
BOOK_GENRE = "LITERATURA"
FIRST_COPY_ID = 8001

RAW_FIELDS = (
    "ejemplares",
    "repeticion",
    "protobuf_bytes",
    "json_bytes",
    "protobuf_gzip_bytes",
    "json_gzip_bytes",
    "ahorro_protobuf_pct",
    "ahorro_protobuf_gzip_pct",
    "python_version",
    "protobuf_version",
    "gzip_level",
)
SUMMARY_FIELDS = (
    "ejemplares",
    "protobuf_mean",
    "protobuf_std",
    "json_mean",
    "json_std",
    "protobuf_gzip_mean",
    "protobuf_gzip_std",
    "json_gzip_mean",
    "json_gzip_std",
    "ahorro_mean_pct",
    "ahorro_gzip_mean_pct",
)


if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

try:
    import catalogo_pb2
except ModuleNotFoundError as exc:
    if exc.name == "google" or (exc.name and exc.name.startswith("google.protobuf")):
        raise SystemExit(
            "Falta protobuf o su versión es incompatible con los stubs. "
            "Instala las dependencias con: "
            "python -m pip install -r docs/experimentos/requirements.txt"
        ) from exc
    raise

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    if exc.name and exc.name.startswith("matplotlib"):
        raise SystemExit(
            "Falta matplotlib. Instala las dependencias con: "
            "python -m pip install -r docs/experimentos/requirements.txt"
        ) from exc
    raise


def build_equivalent_book(exemplar_count: int) -> tuple[catalogo_pb2.LibroDTO, dict[str, Any]]:
    """Crea LibroDTO y JSON con los mismos campos y valores lógicos."""
    if exemplar_count <= 0:
        raise ValueError("La cantidad de ejemplares debe ser mayor que cero.")

    exemplars_json = []
    for index in range(exemplar_count):
        exemplar_id = FIRST_COPY_ID + index
        state = "PRESTADO" if (index + 1) % 4 == 0 else "DISPONIBLE"
        exemplars_json.append(
            {
                "id": exemplar_id,
                "libro_id": BOOK_ID,
                "estado": state,
            }
        )

    available_count = sum(
        exemplar["estado"] == "DISPONIBLE" for exemplar in exemplars_json
    )
    json_book: dict[str, Any] = {
        "id": BOOK_ID,
        "titulo": BOOK_TITLE,
        "autor": BOOK_AUTHOR,
        "genero": BOOK_GENRE,
        "total_ejemplares": exemplar_count,
        "ejemplares_disponibles": available_count,
        "ejemplares": exemplars_json,
    }

    protobuf_book = catalogo_pb2.LibroDTO(
        id=BOOK_ID,
        titulo=BOOK_TITLE,
        autor=BOOK_AUTHOR,
        genero=BOOK_GENRE,
        total_ejemplares=exemplar_count,
        ejemplares_disponibles=available_count,
        ejemplares=[
            catalogo_pb2.EjemplarDTO(
                id=exemplar["id"],
                libro_id=exemplar["libro_id"],
                estado=exemplar["estado"],
            )
            for exemplar in exemplars_json
        ],
    )

    if protobuf_to_dict(protobuf_book) != json_book:
        raise RuntimeError(
            f"Los datos JSON y Protobuf no son equivalentes para N={exemplar_count}."
        )
    return protobuf_book, json_book


def protobuf_to_dict(book: catalogo_pb2.LibroDTO) -> dict[str, Any]:
    """Convierte los campos del mensaje al mismo mapa usado por el JSON."""
    return {
        "id": book.id,
        "titulo": book.titulo,
        "autor": book.autor,
        "genero": book.genero,
        "total_ejemplares": book.total_ejemplares,
        "ejemplares_disponibles": book.ejemplares_disponibles,
        "ejemplares": [
            {"id": item.id, "libro_id": item.libro_id, "estado": item.estado}
            for item in book.ejemplares
        ],
    }


def serialize_sizes(
    protobuf_book: catalogo_pb2.LibroDTO, json_book: dict[str, Any]
) -> dict[str, int]:
    """Serializa ambos formatos y los comprime con parámetros iguales."""
    protobuf_bytes = protobuf_book.SerializeToString()
    json_bytes = json.dumps(
        json_book,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    return {
        "protobuf_bytes": len(protobuf_bytes),
        "json_bytes": len(json_bytes),
        "protobuf_gzip_bytes": len(
            gzip.compress(protobuf_bytes, compresslevel=GZIP_LEVEL, mtime=0)
        ),
        "json_gzip_bytes": len(
            gzip.compress(json_bytes, compresslevel=GZIP_LEVEL, mtime=0)
        ),
    }


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    """Sobrescribe el CSV de salida de forma reproducible."""
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def collect_raw_results() -> list[dict[str, Any]]:
    """Mide cada tamaño 30 veces y verifica que el tamaño no varíe."""
    raw_rows: list[dict[str, Any]] = []
    protobuf_version = version("protobuf")
    python_version = platform.python_version()
    size_fields = (
        "protobuf_bytes",
        "json_bytes",
        "protobuf_gzip_bytes",
        "json_gzip_bytes",
    )

    for exemplar_count in SIZES:
        protobuf_book, json_book = build_equivalent_book(exemplar_count)
        repetitions = [
            serialize_sizes(protobuf_book, json_book)
            for _ in range(REPETITIONS)
        ]

        for field in size_fields:
            observed_sizes = {measurement[field] for measurement in repetitions}
            if len(observed_sizes) != 1:
                raise RuntimeError(
                    f"El tamaño {field} varió entre repeticiones para "
                    f"N={exemplar_count}: {sorted(observed_sizes)}"
                )

        for repetition, measurement in enumerate(repetitions, start=1):
            json_size = measurement["json_bytes"]
            json_gzip_size = measurement["json_gzip_bytes"]
            raw_rows.append(
                {
                    "ejemplares": exemplar_count,
                    "repeticion": repetition,
                    **measurement,
                    "ahorro_protobuf_pct": (
                        (json_size - measurement["protobuf_bytes"]) / json_size * 100
                    ),
                    "ahorro_protobuf_gzip_pct": (
                        (json_gzip_size - measurement["protobuf_gzip_bytes"])
                        / json_gzip_size
                        * 100
                    ),
                    "python_version": python_version,
                    "protobuf_version": protobuf_version,
                    "gzip_level": GZIP_LEVEL,
                }
            )

    return raw_rows


def summarize_results(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calcula medias y desviaciones estándar poblacionales por tamaño."""
    summary_rows = []
    metric_fields = (
        "protobuf_bytes",
        "json_bytes",
        "protobuf_gzip_bytes",
        "json_gzip_bytes",
    )

    for exemplar_count in SIZES:
        rows = [row for row in raw_rows if row["ejemplares"] == exemplar_count]
        summary: dict[str, Any] = {"ejemplares": exemplar_count}
        for field in metric_fields:
            values = [row[field] for row in rows]
            summary[f"{field.removesuffix('_bytes')}_mean"] = statistics.mean(values)
            summary[f"{field.removesuffix('_bytes')}_std"] = statistics.pstdev(values)

        summary["ahorro_mean_pct"] = statistics.mean(
            row["ahorro_protobuf_pct"] for row in rows
        )
        summary["ahorro_gzip_mean_pct"] = statistics.mean(
            row["ahorro_protobuf_gzip_pct"] for row in rows
        )
        summary_rows.append(summary)

    return summary_rows


def write_chart(summary_rows: list[dict[str, Any]], path: Path) -> None:
    """Genera el gráfico comparativo en formato PNG."""
    exemplars = [row["ejemplares"] for row in summary_rows]
    series = (
        ("json_mean", "JSON UTF-8", "o", "-"),
        ("protobuf_mean", "Protocol Buffers", "s", "-"),
        ("json_gzip_mean", "JSON + gzip", "o", "--"),
        ("protobuf_gzip_mean", "Protocol Buffers + gzip", "s", "--"),
    )

    figure, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    axis.set_xscale("log")
    for field, label, marker, linestyle in series:
        axis.plot(
            exemplars,
            [row[field] for row in summary_rows],
            label=label,
            marker=marker,
            linestyle=linestyle,
            linewidth=2,
            markersize=5,
        )

    axis.set_title("Tamaño serializado de LibroDTO por cantidad de ejemplares")
    axis.set_xlabel("Cantidad de ejemplares (escala logarítmica)")
    axis.set_ylabel("Tamaño serializado (bytes)")
    axis.set_xticks(exemplars, labels=[str(value) for value in exemplars])
    axis.minorticks_off()
    axis.grid(True, linestyle=":", alpha=0.55)
    axis.legend()
    figure.savefig(path, dpi=180, format="png")
    plt.close(figure)


def main() -> None:
    """Ejecuta el experimento y reemplaza sus resultados previos."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_rows = collect_raw_results()
    summary_rows = summarize_results(raw_rows)

    raw_path = OUTPUT_DIR / "resultados.csv"
    summary_path = OUTPUT_DIR / "resultados_resumen.csv"
    chart_path = OUTPUT_DIR / "resultados.png"

    write_csv(raw_path, RAW_FIELDS, raw_rows)
    write_csv(summary_path, SUMMARY_FIELDS, summary_rows)
    write_chart(summary_rows, chart_path)

    print(
        f"Mediciones guardadas: {len(raw_rows)} filas; "
        f"{len(SIZES)} tamaños x {REPETITIONS} repeticiones."
    )
    for row in summary_rows:
        print(
            "N={:>3}: protobuf={:>6.1f} B, JSON={:>6.1f} B, "
            "protobuf+gzip={:>6.1f} B, JSON+gzip={:>6.1f} B, "
            "ahorros={:>5.1f}% / {:>5.1f}%".format(
                row["ejemplares"],
                row["protobuf_mean"],
                row["json_mean"],
                row["protobuf_gzip_mean"],
                row["json_gzip_mean"],
                row["ahorro_mean_pct"],
                row["ahorro_gzip_mean_pct"],
            )
        )
    print(f"CSV crudo: {raw_path}")
    print(f"CSV resumen: {summary_path}")
    print(f"Gráfico: {chart_path}")


if __name__ == "__main__":
    main()
