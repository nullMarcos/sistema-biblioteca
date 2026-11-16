FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements/constraints.txt /app/requirements/constraints.txt
COPY bff_service/requirements.txt /app/bff_service/requirements.txt
RUN pip install --no-cache-dir -r /app/bff_service/requirements.txt && pip check
COPY contracts/catalogo.proto /app/contracts/catalogo.proto
COPY scripts/generar_protos.py /app/scripts/generar_protos.py
RUN python scripts/generar_protos.py --output /app/protos
COPY bff_service/main.py /app/main.py
COPY bff_service/src /app/src

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "64"]
