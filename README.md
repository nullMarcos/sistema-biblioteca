# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción

Sistema distribuido de gestión de biblioteca basado en una arquitectura desacoplada de microservicios, desarrollado en el marco de la asignatura de **Integración de Sistemas** (Universidad de Concepción). El sistema resuelve la administración de socios, catálogo bibliográfico, inventario físico de ejemplares, verificación de bloqueos y multas, y el ciclo de vida completo de préstamos y devoluciones de libros.

La solución está compuesta por tres servicios autónomos con persistencia independiente y comunicación distribuida:

- **`prestamos_service` (API REST Pública)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Expone las rutas de negocio externas (`/v1/socios` y `/v1/prestamos`), implementa autenticación estricta basada en cabeceras `X-API-Key`, gestiona su propia base de datos SQLite (`prestamos.db`), incorpora hipermedios dinámicos (**HATEOAS**) para navegación de estado de préstamos, y actúa como cliente gRPC de Catálogo integrando patrones avanzados de resiliencia (**Circuit Breaker** y **reintentos con backoff exponencial**), estandarización integral de errores según el contrato OpenAPI (`{codigo, mensaje}`) y chequeo de salud dependiente (`/health`).
- **`catalogo_service` (Microservicio Interno gRPC)**: Desarrollado sobre el runtime oficial de **gRPC** en Python y **SQLAlchemy 2.0**. Administra el catálogo de libros y el inventario de ejemplares en una base de datos SQLite aislada (`catalogo.db`). Proporciona procedimientos RPC para consulta de disponibilidad, listado con filtros (género y disponibilidad), y reserva (`ReservarEjemplar`) y liberación (`LiberarEjemplar`) atómicas protegidas mediante **bloqueo pesimista** (`with_for_update`) para prevenir sobreventa y condiciones de carrera concurrentes, además de apagado ordenado (*graceful shutdown*).
- **`sanciones_service` (Microservicio REST de Verificación y Resiliencia)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Administra bloqueos activos y multas pendientes en una base de datos SQLite propia (`sanciones.db`). Implementa un **Módulo de Inyección de Caos** en vivo (`/chaos/delay` y `/chaos/fault`) para inducir latencia y caídas controladas, e incorpora una suite de resiliencia en su cliente consumidor: **Timeout estricto de 1.5s**, **Circuit Breaker** de 3 estados con *fail-fast*, **Bulkhead** de concurrencia acotada a 5 llamadas simultáneas y **Fallback elegante** con degradación a `ESTADO: EN_REVISION_MANUAL`.

## Arquitectura

- **`prestamos_service`**: FastAPI/REST en el puerto interno `8000` (mapeado al host `8000:8000`), protegido con API Key; persistencia propia en `prestamos.db`.
- **`catalogo_service`**: Servidor gRPC en el puerto `50051`; persistencia propia en `catalogo.db`.
- **`sanciones_service`**: FastAPI/REST en el puerto interno `8000` (mapeado al host `8001:8000` para desarrollo local); persistencia propia en `sanciones.db`.
- **Patrón Database-per-Service**: Cada microservicio administra su propio almacenamiento SQLite de forma exclusiva (3 bases de datos independientes: `prestamos.db`, `catalogo.db`, `sanciones.db` montadas en volúmenes Docker). No existen llaves foráneas directas (`ForeignKey`) ni cruces entre bases de datos; la correlación se efectúa mediante identificadores lógicos (`libro_id`, `ejemplar_id`) y una clave natural de negocio compartida (**`rut`**) para la identidad de socios sin acoplar secuencias numéricas.
- **Comunicación y Red**: Todos los servicios conviven dentro de la red aislada de Docker Compose (`biblioteca_net`). Las operaciones entre Préstamos y Catálogo se realizan síncronamente vía gRPC sobre HTTP/2 con Protocol Buffers v3, mientras que la verificación de Sanciones se realiza vía HTTP REST con patrones de resiliencia.

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
├── scripts/
│   └── test_resilience_sanciones.py # Demostración automatizada de resiliencia 
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
├── prestamos_service/               # API pública REST de socios y préstamos
│   ├── Dockerfile                   # Imagen Docker optimizada para Python/Uvicorn
│   ├── requirements.txt             # Dependencias (FastAPI, Uvicorn, SQLAlchemy, gRPC)
│   ├── main.py                      # Aplicación FastAPI, lifespan, handlers y health check
│   ├── protos/                      # Stubs Python del cliente gRPC
│   └── src/
│       ├── auth.py                  # Validación de autenticación mediante header X-API-Key
│       ├── circuit_breaker.py       # Patrón Circuit Breaker para protección ante fallas de Catálogo
│       ├── database.py              # Conexión, sesiones de SQLite y dependencia get_db
│       ├── grpc_client.py           # Cliente gRPC, política de reintentos y traducción de errores
│       ├── models.py                # Modelos ORM Socio y Prestamo
│       ├── schemas.py               # Esquemas Pydantic v2 (DTOs de entrada y salida con RUT)
│       ├── seed.py                  # Carga de datos semilla de socios y préstamos
│       └── routers/                 # Endpoints modulares (/v1/socios y /v1/prestamos)
└── sanciones_service/               # Servicio de sanciones, multas y resiliencia
    ├── Dockerfile                   # Imagen Docker optimizada para Python/Uvicorn
    ├── requirements.txt             # Dependencias (FastAPI, Uvicorn, SQLAlchemy, Pydantic)
    ├── main.py                      # Aplicación FastAPI, endpoints de sanciones, caos y health check
    └── src/
        ├── database.py              # Conexión SQLite y dependencia get_db
        ├── models.py                # Modelos ORM Socio, Bloqueo y Multa
        ├── schemas.py               # Esquemas Pydantic v2 (SancionSocioResponse, BloqueoOut, MultaOut)
        ├── seed.py                  # Carga de datos semilla de socios, bloqueos y multas
        ├── client.py                # Cliente SancionesResilientClient (Timeout, CB, Bulkhead, Fallback)
        └── resilience/              # Módulo de patrones de resiliencia
            ├── circuit_breaker.py   # Circuit Breaker de 3 estados (CLOSED, OPEN, HALF-OPEN)
            └── bulkhead.py          # Aislamiento de concurrencia acotada (máx 5)
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
Compose construye y levanta los tres servicios (`catalogo`, `prestamos` y `sanciones`); en el primer inicio las bases de datos SQLite se crean y se cargan automáticamente los datos semilla de prueba. Los volúmenes Docker (`catalogo_data`, `prestamos_data`, `sanciones_data`) conservan los datos entre reinicios.

Para detener los servicios conservando los datos:

```bash
docker compose down
```

Para volver a un estado local limpio:

```bash
docker compose down -v
```

`-v` también elimina los volúmenes locales de las tres bases de datos (socios, préstamos, libros, ejemplares, bloqueos y multas); al levantar de nuevo se vuelven a cargar las semillas. No usarlo si se desean conservar esos datos.

## Autenticación

Las rutas de socios y préstamos en `prestamos_service` requieren el header **`X-API-Key`**. En `docker-compose.yml` está configurada la clave de desarrollo `biblioteca_dev_key_2026`, usada cuando `API_KEY` no está definida. Puedes cambiarla creando `.env` desde `.env.example` y asignando tu valor:

```bash
cp .env.example .env
# Editar API_KEY en .env
```

La clave incluida en Compose es solo para desarrollo/evaluación; no es una credencial segura para producción. `/docs`, `/openapi.json` y `/health` de ambos servicios, así como las rutas internas de `sanciones_service` se pueden consultar sin clave; las operaciones protegidas invocadas desde Swagger de préstamos requieren autorizarse con la clave.

## URLs del Sistema

| Servicio / Recurso | Dirección | Autenticación | Descripción |
|---|---|---|---|
| **Swagger UI (Préstamos)** | <http://localhost:8000/docs> | Pública (endpoints protegidos requieren API Key) | Documentación interactiva de Préstamos y Socios |
| **OpenAPI JSON (Préstamos)** | <http://localhost:8000/openapi.json> | Pública | Especificación OpenAPI de Préstamos |
| **Health Check (Préstamos)** | <http://localhost:8000/health> | Pública | Chequeo de salud dependiente (BD, gRPC y Circuit Breaker) |
| **Socios** | <http://localhost:8000/v1/socios> | `X-API-Key` | CRUD y listado de socios con filtro por RUT |
| **Préstamos** | <http://localhost:8000/v1/prestamos> | `X-API-Key` | Registro, listado y devolución con HATEOAS |
| **Catálogo gRPC** | `localhost:50051` | gRPC interno | Procedimientos RPC de inventario y reservas |
| **Swagger UI (Sanciones)** | <http://localhost:8001/docs> | Pública | Documentación interactiva de Sanciones y Caos |
| **OpenAPI JSON (Sanciones)** | <http://localhost:8001/openapi.json> | Pública | Especificación OpenAPI de Sanciones |
| **Health Check (Sanciones)** | <http://localhost:8001/health> | Pública | Chequeo de salud de base de datos de Sanciones |
| **Consultar Sanciones Socio** | <http://localhost:8001/v1/sanciones/socios/{id_o_rut}> | Pública | Verificación de bloqueos activos y multas por RUT o ID |
| **Listar Sanciones** | <http://localhost:8001/v1/sanciones/socios> | Pública | Listado de estado de sanciones (con filtro opcional `?rut=`) |
| **Módulo de Caos: Latencia** | <http://localhost:8001/chaos/delay?seconds=X> | Pública | Inducción de retardo artificial en llamadas |
| **Módulo de Caos: Fallas** | <http://localhost:8001/chaos/fault?status=500> | Pública | Inducción de error HTTP forzado |
| **Módulo de Caos: Reset** | <http://localhost:8001/chaos/reset> | Pública | Restablece el servicio a la normalidad |
| **Módulo de Caos: Estado** | <http://localhost:8001/chaos/status> | Pública | Consulta métricas del módulo de caos |
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

### Ejecución Local de los Microservicios (sin Docker)

Para ejecutar cualquiera de los microservicios de manera independiente en la máquina local:

```bash
# 1. Catálogo (gRPC en puerto 50051)
python catalogo_service/main.py

# 2. Préstamos (REST en puerto 8000)
API_KEY=biblioteca_dev_key_2026 python -m uvicorn main:app --app-dir prestamos_service --reload --port 8000

# 3. Sanciones (REST en puerto 8001)
python -m uvicorn main:app --app-dir sanciones_service --reload --port 8001
```

### Inicialización y Sembrado de las Bases de Datos

Cada servicio cuenta con su script de poblado inicial para su base SQLite independiente:

```bash
python catalogo_service/src/seed.py --reset
API_KEY=biblioteca_dev_key_2026 python prestamos_service/src/seed.py --reset
python sanciones_service/src/seed.py --reset
```

### Funcionalidades y Patrones Implementados

- **Microservicio Sanciones y Resiliencia (Integrante 3):**
  - **Identificador de Negocio Compartido (`rut`):** Correlación de socios entre `prestamos_service` y `sanciones_service` mediante clave natural única (ej. `11.111.111-1`), desacoplando IDs autonuméricos locales.
  - **Módulo de Caos en Vivo:** Endpoints `/chaos/delay?seconds=X` y `/chaos/fault?status=500` para inducir lentitud y fallas controladas, además de `/chaos/reset` y `/chaos/status`.
  - **Timeout:** Tiempo límite estricto de 1.5s en llamadas remotas hacia Sanciones, conteniendo la lentitud artificial.
  - **Circuit Breaker:** Máquina de 3 estados (`CLOSED`, `OPEN`, `HALF_OPEN`) en `sanciones_service/src/resilience/circuit_breaker.py`. Tras 3 fallos consecutivos transiciona a `OPEN` respondiendo inmediatamente (*fail-fast* en <1ms) sin tocar la red.
  - **Bulkhead (Aislamiento de Concurrencia):** Control de concurrencia acotado a máximo 5 llamadas simultáneas hacia Sanciones (`sanciones_service/src/resilience/bulkhead.py`) con rechazo inmediato de saturación para evitar agotamiento de hilos en el consumidor.
  - **Fallback Elegante:** Regla de negocio que no rechaza el préstamo a ciegas ante caídas o timeouts, degradando la respuesta a `ESTADO: EN_REVISION_MANUAL`.
  - **Script de Demostración del Escenario 3:**
    ```bash
    python scripts/test_resilience_sanciones.py
    ```
    Ejecuta las 5 fases obligatorias (camino feliz, corte por timeout a 1.5s, disyuntor abierto fail-fast, rechazos por bulkhead y recuperación automática a `CLOSED`).

- **Métodos Core gRPC (Catálogo):**
  - `ReservarEjemplar(ReservaRequest)`: Reserva atómica de ejemplar disponible (`DISPONIBLE` -> `PRESTADO`).
  - `LiberarEjemplar(LiberarRequest)`: Liberación de ejemplar prestado (`PRESTADO` -> `DISPONIBLE`).
  - `ConsultarDisponibilidad(ConsultaRequest)`: Consulta de conteo total y disponibles por libro.
  - `ListarCatalogo(CatalogoRequest)`: Lista el catálogo con filtros opcionales de género y disponibilidad.
  - `ObtenerLibro(LibroRequest)`: Obtiene la información detallada de un libro y sus ejemplares.
- **HATEOAS (Préstamos):** Navegación de estado dinámica en la API REST de Préstamos (`_links` dinámico para acción `DELETE` al estar en estado `ACTIVO`).
- **Resiliencia Catálogo (ADR-004):** Circuit Breaker y política de reintentos con backoff exponencial sobre llamadas gRPC hacia Catálogo.
- **Manejo Estandarizado de Errores `{codigo, mensaje}`:** Respuestas estandarizadas en todas las APIs bajo el contrato OpenAPI.
- **Health Checks:** Endpoints `/health` dedicados en Préstamos y Sanciones.
- **Concurrencia Atómica:** Bloqueo pesimista mediante `.with_for_update()` en SQLAlchemy para reservas y liberaciones en Catálogo.
- **Apagado Ordenado (Graceful Shutdown):** Manejo de señales `SIGTERM` y `SIGINT` en los servicios.

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
