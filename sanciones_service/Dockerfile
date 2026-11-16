FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATABASE_URL=sqlite:////app/data/sanciones.db

WORKDIR /app

# Asegurar existencia del directorio de persistencia para la base de datos
RUN mkdir -p /app/data

COPY requirements/constraints.txt /app/requirements/constraints.txt
COPY sanciones_service/requirements.txt /app/sanciones_service/requirements.txt
RUN pip install --no-cache-dir -r /app/sanciones_service/requirements.txt && pip check

COPY sanciones_service/main.py /app/main.py
COPY sanciones_service/src /app/src

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
