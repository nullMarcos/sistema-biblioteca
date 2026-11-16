FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Asegurar existencia del directorio de persistencia para la base de datos
RUN mkdir -p /app/data

COPY requirements/constraints.txt /app/requirements/constraints.txt
COPY catalogo_service/requirements.txt /app/catalogo_service/requirements.txt
RUN pip install --no-cache-dir -r /app/catalogo_service/requirements.txt && pip check

COPY contracts/catalogo.proto /app/contracts/catalogo.proto
COPY scripts/generar_protos.py /app/scripts/generar_protos.py
COPY scripts/migrar_sqlite.py /app/scripts/migrar_sqlite.py
RUN python scripts/generar_protos.py --output /app/protos
COPY catalogo_service/main.py /app/main.py
COPY catalogo_service/src /app/src
COPY catalogo_service/migrations /app/migrations

EXPOSE 50051

CMD ["python", "main.py"]
