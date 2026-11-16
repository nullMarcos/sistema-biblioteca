FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements/constraints.txt /app/requirements/constraints.txt
COPY gateway_service/requirements.txt /app/gateway_service/requirements.txt
RUN pip install --no-cache-dir -r /app/gateway_service/requirements.txt && pip check

COPY gateway_service/main.py /app/main.py

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
