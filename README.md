# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción

Sistema distribuido de gestión de biblioteca basado en una arquitectura desacoplada de microservicios, desarrollado en el marco de la asignatura de **Integración de Sistemas** (Universidad de Concepción). El sistema resuelve la administración de socios, catálogo bibliográfico, inventario físico de ejemplares y el ciclo de vida completo de préstamos y devoluciones de libros.

La solución está compuesta por dos servicios autónomos con persistencia independiente y comunicación síncrona:

- **`prestamos_service` (API REST Pública)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Expone las rutas de negocio externas (`/v1/socios` y `/v1/prestamos`), implementa autenticación estricta basada en cabeceras `X-API-Key`, gestiona su propia base de datos SQLite (`prestamos.db`), incorpora hipermedios dinámicos (**HATEOAS**) para navegación de estado de préstamos, y actúa como cliente gRPC de Catálogo integrando patrones avanzados de resiliencia (**Circuit Breaker** y **reintentos con backoff exponencial**), estandarización integral de errores según el contrato OpenAPI (`{codigo, mensaje}`) y chequeo de salud dependiente (`/health`).
- **`catalogo_service` (Microservicio Interno gRPC)**: Desarrollado sobre el runtime oficial de **gRPC** en Python y **SQLAlchemy 2.0**. Administra el catálogo de libros y el inventario de ejemplares en una base de datos SQLite aislada (`catalogo.db`). Proporciona procedimientos RPC para consulta de disponibilidad, listado con filtros (género y disponibilidad), y reserva (`ReservarEjemplar`) y liberación (`LiberarEjemplar`) atómicas protegidas mediante **bloqueo pesimista** (`with_for_update`) para prevenir sobreventa y condiciones de carrera concurrentes, además de apagado ordenado (*graceful shutdown*).

## Arquitectura

- **`prestamos_service`**: FastAPI/REST expuesto en el puerto `8000`, protegido con API Key; persistencia en base de datos SQLite local (`prestamos.db`).
- **`catalogo_service`**: Servidor gRPC en el puerto `50051`; persistencia en base de datos SQLite local (`catalogo.db`).
- **Patrón Database-per-Service**: Cada microservicio administra su propio almacenamiento de forma exclusiva. No existen llaves foráneas directas (`ForeignKey`) ni cruces entre bases de datos; la correlación se efectúa mediante identificadores lógicos externos (`libro_id`, `ejemplar_id`).
- **Comunicación y Red**: Ambos servicios se comunican a través de la red bridge de Docker Compose (`biblioteca_net`). Las operaciones entre Préstamos y Catálogo se realizan síncronamente vía gRPC sobre HTTP/2 utilizando serialización binaria con Protocol Buffers v3.

## Estructura del repositorio

```text
sistema-biblioteca/
├── docker-compose.yml              # Orquestación de servicios, puertos, red y volúmenes
├── .env.example                    # Plantilla de variables de entorno (API_KEY)
├── README.md                       # Documentación principal, ejecución y pruebas
├── contracts/                      # Contratos compartidos entre consumidores y servicios
│   ├── openapi.yaml                # Especificación formal de la API REST (OpenAPI 3.0.4)
│   └── catalogo.proto              # Contrato de servicio y mensajes Protobuf/gRPC v3
├── docs/
│   ├── adr/                         # Decisiones de arquitectura (ADR-001 a ADR-004)
│   └── experimentos/                # Experimento ABET 6 y artefactos generados
│       ├── experimento.py           # Script de medición JSON/Protobuf, con y sin gzip
│       ├── requirements.txt         # Dependencias locales del experimento
│       ├── README.md                # Método, hipótesis y análisis experimental
│       ├── resultados.csv           # Mediciones crudas
│       ├── resultados_resumen.csv   # Resumen estadístico
│       └── resultados.png           # Gráfico comparativo generado
├── catalogo_service/                # Servicio interno de inventario (gRPC)
│   ├── Dockerfile                   # Imagen Docker optimizada para Python
│   ├── requirements.txt             # Dependencias del servicio (gRPC, SQLAlchemy, Protobuf)
│   ├── main.py                      # Inicio del servidor gRPC y apagado ordenado (graceful shutdown)
│   ├── protos/                      # Stubs Python generados a partir de catalogo.proto
│   └── src/
│       ├── database.py              # Conexión y sesiones de SQLite/SQLAlchemy
│       ├── models.py                # Modelos ORM Libro y Ejemplar (con índices)
│       ├── seed.py                  # Carga de datos semilla de libros y ejemplares
│       └── server.py                # Implementación de RPCs del servicio Catalogo
└── prestamos_service/               # API pública REST de socios y préstamos
    ├── Dockerfile                   # Imagen Docker optimizada para Python/Uvicorn
    ├── requirements.txt             # Dependencias (FastAPI, Uvicorn, SQLAlchemy, gRPC)
    ├── main.py                      # Aplicación FastAPI, lifespan, handlers y health check
    ├── protos/                      # Stubs Python del cliente gRPC
    └── src/
        ├── auth.py                  # Validación de autenticación mediante header X-API-Key
        ├── circuit_breaker.py       # Patrón Circuit Breaker para protección ante fallas de Catálogo
        ├── database.py              # Conexión, sesiones de SQLite y dependencia get_db
        ├── grpc_client.py           # Cliente gRPC, política de reintentos y traducción de errores
        ├── models.py                # Modelos ORM Socio y Prestamo
        ├── schemas.py               # Esquemas Pydantic v2 (DTOs de entrada y salida)
        ├── seed.py                  # Carga de datos semilla de socios y préstamos
        └── routers/                 # Endpoints modulares (/v1/socios y /v1/prestamos)
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
- **HATEOAS (Opcional 3):** Se implementó navegación de estado dinámica en la API REST de Préstamos. Las respuestas incluyen un bloque `_links` que expone dinámicamente el hipervínculo de la acción de devolver (método `DELETE`) únicamente cuando el estado del préstamo es `ACTIVO`.
- **Resiliencia y Tolerancia a Fallas (ADR-004):**
  - **Circuit Breaker:** Disyuntor implementado en memoria (`prestamos_service/src/circuit_breaker.py`) que protege las llamadas hacia Catálogo. Ante 3 fallos consecutivos transiciona a `OPEN` respondiendo de forma inmediata (*fail-fast*) con HTTP 503 (`CIRCUITO_ABIERTO`), y tras 10 segundos transiciona a `HALF_OPEN` para probar la recuperación del servicio.
  - **Reintentos con Backoff Exponencial:** Política de hasta 2 reintentos con esperas de 0.1s y 0.2s aplicada exclusivamente ante fallas transitorias de conectividad (`UNAVAILABLE`, `DEADLINE_EXCEEDED`).
  - **Mapeo Granular de Excepciones gRPC a HTTP:** Traducción estricta de códigos gRPC a respuestas HTTP normalizadas (`NOT_FOUND` -> 404, `INVALID_ARGUMENT` -> 400, falta de stock -> 409, timeout -> 504).
- **Manejo Estandarizado de Errores `{codigo, mensaje}`:** Manejadores globales de excepciones en FastAPI que aseguran que todas las respuestas de error (400, 401, 404, 409, 502, 503, 504) cumplan estrictamente con el esquema definido en `openapi.yaml`.
- **Health Check Integrado y Dependiente:** Endpoint `/health` que comprueba el estado de la base de datos local SQLite, la conectividad con Catálogo gRPC y el estado del Circuit Breaker, respondiendo HTTP 200 (`ok`) o HTTP 503 (`degraded`/`unhealthy`).
- **Concurrencia Atómica:** Bloqueo pesimista mediante `.with_for_update()` en SQLAlchemy para transacciones de reserva y liberación en `catalogo_service`.
- **Apagado Ordenado (Graceful Shutdown):** Manejo de señales `SIGTERM` y `SIGINT` en ambos microservicios para cierre limpio de conexiones y recursos.

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

En concordancia con las buenas prácticas de integridad, reproducibilidad y transparencia académica/profesional, a continuación se detalla cómo se utilizaron herramientas de Inteligencia Artificial durante las diferentes etapas del proyecto:

### 1. Herramientas Utilizadas
- **Antigravity / Gemini / OPENCODE**: Empleado como asistente de ingeniería de software en modo agente para análisis del repositorio, verificación cruzada de contratos, refinamiento de documentación técnica y asistencia en la implementación de patrones de resiliencia y concurrencia.

### 2. Tareas Asistidas y Casos de Uso
- **Contratos de interfaz:** Apoyo en la revisión y alineación de los contratos OpenAPI y Protocol Buffers.
- **Resiliencia y manejo de errores:** Apoyo en el diseño del Circuit Breaker, reintentos con backoff y manejo unificado de excepciones.
- **HATEOAS:** Apoyo en el diseño e integración de enlaces según el estado de los recursos.
- **Documentación técnica:** Apoyo en la elaboración de ADRs, documentación del sistema y diseño del benchmark Protobuf vs. JSON.

### 3. Supervisión, Validación y Criterio Humano
- **Revisión Crítica y Adaptación:** Todo fragmento de código, esquema o texto propuesto por los asistentes fue exhaustivamente analizado, revisado, refactorizado y probado por los integrantes del equipo antes de su integración definitiva.
- **Decisiones Arquitectónicas Propias:** Las decisiones de diseño fundamentales (aislamiento estricto de bases de datos mediante el patrón *Database-per-Service*, selección del estilo de integración síncrona REST/gRPC, bloqueo pesimista `with_for_update` en SQLAlchemy y dimensionamiento de umbrales del disyuntor) fueron evaluadas y consensuadas por los miembros del equipo.
- **Verificación Práctica:** La corrección y fiabilidad del software fueron validadas mediante la ejecución exitosa de la suite de pruebas unitarias y el despliegue reproducible en contenedores Docker.
