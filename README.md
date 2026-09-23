# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción

Sistema de biblioteca compuesto por una API REST de Préstamos y un servicio interno gRPC de Catálogo. Préstamos administra socios y préstamos; Catálogo administra libros y ejemplares.

## Arquitectura

- `prestamos_service`: FastAPI/REST en el puerto `8000`, protegido con API Key; usa su base de datos SQLite.
- `catalogo_service`: gRPC en el puerto `50051`; administra el inventario en su propia base SQLite.
- Ambos servicios se conectan por la red de Docker Compose. El servicio REST llama a Catálogo para reservar y liberar ejemplares.

## Estructura del repositorio

```text
sistema-biblioteca/
├── docker-compose.yml              # Servicios, puertos, red y volúmenes
├── README.md                       # Ejecución, pruebas y documentación del proyecto
├── contracts/                      # Contratos compartidos entre consumidores y servicios
│   ├── openapi.yaml                # Especificación de la API REST
│   └── catalogo.proto              # Servicio y mensajes Protobuf/gRPC
├── docs/
│   ├── adr/                         # Decisiones de arquitectura (ADR-001 a ADR-004)
│   └── experimentos/                # Experimento ABET 6 y artefactos generados
│       ├── experimento.py           # Medición JSON/Protobuf, con y sin gzip
│       ├── README.md                # Método, hipótesis y análisis
│       ├── resultados.csv           # Mediciones crudas
│       ├── resultados_resumen.csv   # Resumen estadístico
│       └── resultados.png           # Gráfico comparativo
├── catalogo_service/                # Servicio interno de inventario (gRPC)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                      # Inicio del servidor gRPC, health y reflection
│   ├── protos/                      # Stubs Python generados
│   └── src/
│       ├── database.py              # Conexión y sesiones de SQLite/SQLAlchemy
│       ├── models.py                # Modelos Libro y Ejemplar
│       ├── seed.py                  # Datos de prueba de libros y ejemplares
│       └── server.py                # Implementación del servicio Catalogo
└── prestamos_service/               # API pública REST de socios y préstamos
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py                      # Aplicación FastAPI y health REST
    ├── protos/                      # Stubs Python del cliente gRPC
    └── src/
        ├── auth.py                  # Validación del header X-API-Key
        ├── circuit_breaker.py       # Protección ante fallas de Catálogo
        ├── database.py              # Conexión y sesiones de SQLite/SQLAlchemy
        ├── grpc_client.py           # Llamadas gRPC y traducción de errores
        ├── models.py                # Modelos Socio y Prestamo
        ├── schemas.py               # Esquemas de entrada y salida
        ├── seed.py                  # Socios y préstamo de prueba
        └── routers/                 # Endpoints /v1/socios y /v1/prestamos
```

## Requisitos

- Git, Docker Engine con Docker Compose v2 y `curl`.
- Python 3 para extraer los identificadores en el flujo de prueba y ejecutar el experimento.

Para clonar desde GitHub:

```bash
git clone https://github.com/nullMarcos/sistema-biblioteca.git
cd sistema-biblioteca
```

## Ejecución con Docker Compose

Desde la raíz del repositorio, se puede revisar la configuración resuelta y levantar ambos servicios:

```bash
docker compose config
docker compose up --build
```
Compose construye y levanta `catalogo` y `prestamos`; en el primer inicio las bases se crean y se cargan datos de prueba. Los volúmenes conservan datos entre reinicios.

Para detener los servicios conservando los datos:

```bash
docker compose down
```

Para volver a un estado local limpio:

```bash
docker compose down -v
```

`-v` también elimina los volúmenes locales de las bases de datos (socios, préstamos, libros y ejemplares); al levantar de nuevo se vuelven a cargar las semillas. No usarlo si se desean conservar esos datos.

## Autenticación

Las rutas de socios y préstamos requieren el header **`X-API-Key`**. En `docker-compose.yml` está configurada la clave de desarrollo `biblioteca_dev_key_2026`, usada cuando `API_KEY` no está definida. Puedes cambiarla creando `.env` desde `.env.example` y asignando tu valor:

```bash
cp .env.example .env
# Editar API_KEY en .env
```

La clave incluida en Compose es solo para desarrollo/evaluación; no es una credencial segura para producción. `/docs`, `/openapi.json` y `/health` se pueden consultar sin clave; las operaciones protegidas invocadas desde Swagger también requieren autorizarse con la clave.

## URLs del Sistema

| Recurso | Dirección | Autenticación |
|---|---|---|
| Swagger UI | <http://localhost:8000/docs> | Página pública; endpoints protegidos requieren API Key |
| OpenAPI JSON | <http://localhost:8000/openapi.json> | Pública |
| Health REST | <http://localhost:8000/health> | Pública |
| Socios | <http://localhost:8000/v1/socios> | `X-API-Key` |
| Préstamos | <http://localhost:8000/v1/prestamos> | `X-API-Key` |
| Catálogo gRPC | `localhost:50051` | gRPC interno |
### Cómo probar los endpoints protegidos

- **Desde Swagger UI**:
  1. Abrir http://localhost:8000/docs en el navegador.
  2. Hacer clic en el botón verde **Authorize** (arriba a la derecha)
  3. Ingresar la clave de desarrollo `biblioteca_dev_key_2026` (o la definida en `.env`).
  4. Probar cualquier endpoint con el botón **Try it out**.

- **Desde la terminal**:

  - **Linux / macOS**:
    ```bash
    curl -H "X-API-Key: biblioteca_dev_key_2026" http://localhost:8000/v1/socios
    ```
  - **Windows (PowerShell)**:
    ```powershell
    curl.exe -H "X-API-Key: biblioteca_dev_key_2026" http://localhost:8000/v1/socios
    ```
---

## Contratos

- [`contracts/openapi.yaml`](contracts/openapi.yaml): contrato OpenAPI 3.0.4 de REST, rutas `/v1`, esquemas, autenticación y respuestas.
- [`contracts/catalogo.proto`](contracts/catalogo.proto): contrato Protobuf v3 del servicio y los mensajes gRPC.

Los stubs Python generados se encuentran en `catalogo_service/protos/` y `prestamos_service/protos/`. Si cambia el `.proto`, instala `grpcio-tools` en el Python local y regenera desde la raíz:

```bash
python3 -m pip install grpcio-tools
python3 -m grpc_tools.protoc -Icontracts --python_out=catalogo_service/protos --grpc_python_out=catalogo_service/protos contracts/catalogo.proto
python3 -m grpc_tools.protoc -Icontracts --python_out=prestamos_service/protos --grpc_python_out=prestamos_service/protos contracts/catalogo.proto
```

No es necesario regenerar stubs si el contrato no cambia. El servicio Catálogo implementa `ConsultarDisponibilidad`, `ReservarEjemplar`, `LiberarEjemplar`, `ListarCatalogo` y `ObtenerLibro`.

### Ejecución Local del Servidor gRPC
Para ejecutar el servidor de Catálogo de manera independiente:
```bash
python3 catalogo_service/main.py
```

### Funcionalidades y Opcionales Implementados
- **Métodos Core gRPC:**
  - `ReservarEjemplar(ReservaRequest)`: Reserva atómica de ejemplar disponible (`DISPONIBLE` -> `PRESTADO`).
  - `LiberarEjemplar(LiberarRequest)`: Liberación de ejemplar prestado (`PRESTADO` -> `DISPONIBLE`).
  - `ConsultarDisponibilidad(ConsultaRequest)`: Consulta de conteo total y disponibles por libro.
  - `ListarCatalogo(CatalogoRequest)`: Lista el catálogo con filtros opcionales de género y disponibilidad.
  - `ObtenerLibro(LibroRequest)`: Obtiene la información detallada de un libro y sus ejemplares.


## Experimento ABET 6

Compara el tamaño de un `LibroDTO` serializado como JSON UTF-8 y Protobuf, con y sin gzip, para varios números de ejemplares. Desde la raíz:

```bash
python3 -m pip install -r docs/experimentos/requirements.txt
python3 docs/experimentos/experimento.py
```

Genera [`resultados.csv`](docs/experimentos/resultados.csv), [`resultados_resumen.csv`](docs/experimentos/resultados_resumen.csv) y [`resultados.png`](docs/experimentos/resultados.png). El método, entorno, hipótesis e interpretación están en [`docs/experimentos/README.md`](docs/experimentos/README.md); el script está en [`docs/experimentos/experimento.py`](docs/experimentos/experimento.py).

## ADR

- [ADR-001 · Estilo de integración y descomposición](docs/adr/ADR-001-estilo-descomposicion.md)
- [ADR-002 · REST hacia afuera, gRPC hacia adentro](docs/adr/ADR-002-rest-vs-grpc.md)
- [ADR-003 · Contrato, versionado y evolución](docs/adr/ADR-003-contratos-versionado.md)
- [ADR-004 · Resiliencia y modos de falla](docs/adr/ADR-004-resiliencia-modos-falla.md)

## Uso de asistentes de IA
