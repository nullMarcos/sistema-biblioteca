# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción

Sistema distribuido de gestión de biblioteca basado en una arquitectura desacoplada de microservicios, desarrollado en el marco de la asignatura de **Integración de Sistemas** (Universidad de Concepción). El sistema resuelve la administración de socios, catálogo bibliográfico, inventario físico de ejemplares, verificación de bloqueos y multas, y el ciclo de vida completo de préstamos y devoluciones de libros.

La solución está compuesta por tres servicios autónomos con persistencia independiente y comunicación distribuida:

- **`prestamos_service` (API REST interna)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Atiende las rutas internas `/v1/socios` y `/v1/prestamos`, protegidas con `X-API-Key`; Gateway las publica con JWT bajo `/api/v1`. Gestiona su propia base SQLite (`prestamos.db`), incorpora HATEOAS y consume Catálogo mediante gRPC con Circuit Breaker y reintentos. Conserva errores `{codigo, mensaje}` y un `/health` interno que informa sus dependencias.
- **`catalogo_service` (Microservicio Interno gRPC)**: Desarrollado sobre el runtime oficial de **gRPC** en Python y **SQLAlchemy 2.0**. Administra el catálogo de libros y el inventario de ejemplares en una base de datos SQLite aislada (`catalogo.db`). Proporciona procedimientos RPC para consulta de disponibilidad, listado con filtros (género y disponibilidad), y reserva (`ReservarEjemplar`) y liberación (`LiberarEjemplar`) atómicas protegidas mediante **bloqueo pesimista** (`with_for_update`) para prevenir sobreventa y condiciones de carrera concurrentes, además de apagado ordenado (*graceful shutdown*).
- **`sanciones_service` (Microservicio REST de Verificación y Resiliencia)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Administra bloqueos activos y multas pendientes en una base de datos SQLite propia (`sanciones.db`). Implementa un **Módulo de Inyección de Caos** en vivo (`/chaos/delay` y `/chaos/fault`) para inducir latencia y caídas controladas, e incorpora una suite de resiliencia en su cliente consumidor: **Timeout estricto de 1.5s**, **Circuit Breaker** de 3 estados con *fail-fast*, **Bulkhead** de concurrencia acotada a 5 llamadas simultáneas y **Fallback elegante** con degradación a `ESTADO: EN_REVISION_MANUAL`.

## Arquitectura

- **`gateway_service`**: única entrada publicada, por defecto `http://localhost:8080`. Autentica mediante JWT, limita peticiones y enruta `/api/v1/socios` y `/api/v1/prestamos`.
- **`prestamos_service`**: FastAPI/REST en `prestamos:8000`, accesible dentro de Compose y protegido con API Key; persistencia propia en `prestamos.db`.
- **`catalogo_service`**: Servidor gRPC en el puerto `50051`; persistencia propia en `catalogo.db`.
- **`sanciones_service`**: FastAPI/REST en `sanciones:8000`, sin puerto publicado al host; persistencia propia en `sanciones.db`.
- **Patrón Database-per-Service**: Cada microservicio administra su propio almacenamiento SQLite de forma exclusiva (3 bases de datos independientes: `prestamos.db`, `catalogo.db`, `sanciones.db` montadas en volúmenes Docker). No existen llaves foráneas directas (`ForeignKey`) ni cruces entre bases de datos; la correlación se efectúa mediante identificadores lógicos (`libro_id`, `ejemplar_id`) y una clave natural de negocio compartida (**`rut`**) para la identidad de socios sin acoplar secuencias numéricas.
- **Comunicación y Red**: Compose crea `<proyecto>_biblioteca_net` y nombres de contenedor propios del proyecto. Préstamos consume `catalogo:50051` mediante gRPC y `sanciones:8000` mediante HTTP REST. Las tres bases usan volúmenes independientes con prefijo del proyecto.

## Estructura del repositorio

```text
sistema-biblioteca/
├── docker-compose.yml              # Orquestación de servicios, puertos, red y volúmenes
├── .env.example                    # Plantilla de API_KEY, JWT_SECRET y GATEWAY_PORT
├── README.md                       # Documentación principal, ejecución y pruebas
├── contracts/                      # Contratos compartidos entre consumidores y servicios
│   ├── openapi.yaml                # Especificación formal de la API REST (OpenAPI 3.0.4)
│   └── catalogo.proto              # Contrato de servicio y mensajes Protobuf/gRPC v3
├── docs/
│   ├── adr/                         # Decisiones de arquitectura (ADR-001 a ADR-005)
│   └── experimentos/                # Experimento ABET 6 y artefactos generados
│       ├── experimento.py           # Script de medición JSON/Protobuf, con y sin gzip
│       ├── requirements.txt         # Dependencias locales del experimento
│       ├── README.md                # Método, hipótesis y análisis experimental
│       ├── resultados.csv           # Mediciones crudas
│       ├── resultados_resumen.csv   # Resumen estadístico
│       └── resultados.png           # Gráfico comparativo generado
├── scripts/
│   ├── test_resilience_sanciones.py # Demostración automatizada de resiliencia 
│   └── test_saga_escenarios.py      # Demostración de escenarios Saga y reporte ABET 6
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
├── prestamos_service/               # API REST interna de socios y préstamos
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
│       ├── routers/                 # Endpoints modulares (/v1/socios y /v1/prestamos)
│       └── saga/                    # Motor de orquestación Saga, compensación LIFO e idempotencia
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

Desde la raíz del repositorio, se puede revisar la configuración resuelta y levantar los cuatro servicios:

```bash
docker compose config
docker compose up --build -d --wait
```
Compose construye Catálogo, Préstamos, Sanciones y Gateway. En el primer inicio las bases SQLite se crean y cargan sus semillas. Los volúmenes conservan datos entre reinicios. El único binding al host es `${GATEWAY_PORT:-8080}:8080`; se puede configurar en `.env` junto con `API_KEY` y `JWT_SECRET` (ver `.env.example`).

Los builds se ejecutan desde la raíz con `docker compose build`, no desde la carpeta individual de cada servicio. Los archivos `Dockerfile.dockerignore` limitan cada contexto al código de ese servicio y sus entradas de build: no incluyen `.env`, BD, pruebas ni stubs preexistentes. Las imágenes base fijan parche y digest multi-arquitectura, y `requirements/constraints.txt` fija las dependencias transitivas. Se conservan Python 3.14 para los servicios de dominio y 3.12 para Gateway.

Los healthchecks de Compose comprueban disponibilidad del servidor. En Préstamos se consulta `/openapi.json` como liveness; `/health` mantiene su diagnóstico de dependencias y puede indicar degradación. Préstamos espera a Catálogo, pero puede arrancar sin Sanciones. `docker compose up --wait` del proyecto completo sí espera la salud de los cuatro contenedores.

Para dos instalaciones independientes, usar nombres de proyecto y puertos distintos, por ejemplo `GATEWAY_PORT=8081 docker compose -p biblioteca-otra up --build -d --wait`. No se renombran ni borran automáticamente volúmenes existentes.

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

El cliente externo obtiene un JWT en `POST /auth/login` de Gateway y lo envía como `Authorization: Bearer TOKEN`. Las cuentas de demostración existentes son `admin/admin123` y `socio1/socio123`. Gateway usa `X-API-Key` como credencial interna hacia Préstamos. En Compose, `API_KEY` usa `biblioteca_dev_key_2026` como valor de desarrollo si no está definida. Puedes configurar los valores creando `.env` desde `.env.example`:

```bash
cp .env.example .env
# Editar API_KEY, JWT_SECRET y GATEWAY_PORT en .env
```

Las claves por defecto y las cuentas anteriores son de demostración. Los endpoints de diagnóstico y caos de los servicios internos se consultan dentro de la red Compose; no tienen un binding público propio.

## URLs del Sistema

| Servicio / Recurso | Dirección | Autenticación | Descripción |
|---|---|---|---|
| **Health Gateway** | <http://localhost:8080/health> | Pública | Liveness del Gateway |
| **Login** | <http://localhost:8080/auth/login> | Credenciales de demostración | POST que entrega JWT |
| **Swagger Gateway** | <http://localhost:8080/docs> | Página pública | Contrato del proxy y login |
| **Socios** | <http://localhost:8080/api/v1/socios> | JWT | CRUD y listado con filtro por RUT |
| **Préstamos** | <http://localhost:8080/api/v1/prestamos> | JWT | Registro, listado y devolución con HATEOAS |

Las URLs anteriores usan el puerto por defecto; sustituirlo por `GATEWAY_PORT` cuando corresponda. Catálogo, Préstamos y Sanciones solo se acceden por DNS interno. Para un diagnóstico HTTP interno, por ejemplo:

```bash
docker compose exec -T sanciones python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health', timeout=3).read().decode())"
```
### Cómo probar los endpoints protegidos

Desde la terminal, obtener el token y consultar socios:

```bash
curl -H 'Content-Type: application/json' -d '{"username":"admin","password":"admin123"}' http://localhost:8080/auth/login
# Copiar el access_token obtenido en la siguiente solicitud.
curl -H "Authorization: Bearer TOKEN" http://localhost:8080/api/v1/socios
```
---

## Contratos

- [`contracts/openapi.yaml`](contracts/openapi.yaml): contrato OpenAPI 3.0.4 de REST, rutas `/v1`, esquemas, autenticación y respuestas.
- [`contracts/catalogo.proto`](contracts/catalogo.proto): contrato Protobuf v3 del servicio y los mensajes gRPC.

Los stubs versionados están en `catalogo_service/protos/` y `prestamos_service/protos/`. El mismo generador se usa en Docker y desarrollo. Preparar el entorno desde la raíz (Python 3.13 para las comprobaciones locales de este bloque):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/generar_protos.py
.venv/bin/python scripts/generar_protos.py --check
```

El check falla si los archivos versionados difieren del compilador fijado; no los corrige silenciosamente. Docker genera sus propios stubs sin copiarlos desde el host. Catálogo implementa `ConsultarDisponibilidad`, `ReservarEjemplar`, `LiberarEjemplar`, `ListarCatalogo` y `ObtenerLibro`.

### Pruebas aisladas y preparación de migraciones

```bash
.venv/bin/python scripts/test_unitarios.py
.venv/bin/python -m pip check
.venv/bin/python scripts/verificar_compose.py
```

El primer runner comprueba deriva y ejecuta, en procesos separados, las suites comunes, de Catálogo y de Préstamos. Usan SQLite temporal, un servidor gRPC local con puerto asignado por el sistema y clientes de prueba; no ejecutan semillas sobre las BD del usuario. Los servicios falsos se limitan a impedir escrituras remotas no esperadas en los casos unitarios de Préstamos.

El runner de Compose usa explícitamente `docker-compose.yml`, sin overrides ni `.env` locales, y fija la URL interna de Sanciones. Crea dos proyectos con nombres/puertos propios, comprueba bindings mediante `docker inspect`, DNS, una lectura gRPC real, separación de volúmenes/datos y arranque de Préstamos con Sanciones detenida. Su limpieza elimina los contenedores, redes, volúmenes e imágenes de esos proyectos desechables. El script antiguo de Gateway admite `BASE_URL` o `GATEWAY_PORT`; su cobertura y limitaciones están registradas en `docs/seguimiento-integrante2.md`.

`scripts/migrar_sqlite.py` prepara el mecanismo de migraciones SQL incrementales para los cambios de esquema posteriores: registra checksum, aplica una sola vez y hace rollback si falla. Todavía no se han añadido migraciones de negocio ni se ha cambiado el esquema de los servicios en C01–C03; `create_all()` sigue inicializando instalaciones nuevas y no se presenta como migración de tablas existentes.

### Ejecución Local de los Microservicios (sin Docker)

Tras instalar las dependencias y generar los stubs como se indica arriba, activar el entorno en cada terminal para la ejecución local:

```bash
source .venv/bin/activate

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

- **Transacción Distribuida con Patrón Saga (Orquestador y Compensaciones):**
  - **Coordinación Centralizada:** Implementación del patrón Saga mediante orquestación (`PrestamoSagaOrchestrator`) para la operación de préstamo, coordinando Catálogo (gRPC), Sanciones (HTTP resiliente) y Préstamos (DB local).
  - **Compensación Semántica en Orden Inverso (LIFO):** Registro de pasos ejecutados en pila. Si el Paso 3 falla (o el Paso 2 detecta sanción activa), el orquestador gatilla la liberación del ejemplar en Catálogo via gRPC (`LiberarEjemplar`), restaurando la consistencia del inventario y evitando reservas huérfanas.
  - **Idempotencia Estricta:** Manejo de llaves únicas `idempotency_key` con tabla de persistencia `saga_transacciones`, retornando resultados precalculados ante reintentos sin duplicar operaciones ni reservas.
  - **Degradación Elegante:** Integración con la regla de negocio de Sanciones; si el verificador no responde o está en fallback, el préstamo se registra con estado `EN_REVISION`.
  - **Script de Demostración de Escenarios 1 y 2 (ABET 6):**
    ```bash
    python scripts/test_saga_escenarios.py
    ```
    Ejecuta de forma automatizada y reproducible: Escenario 1 (Camino Feliz), deduplicación por Idempotencia, Escenario 2 (Fallo forzado en Paso 3 y compensación inversa en Catálogo) y rechazo inmediato de socios sancionados, generando el reporte cuantitativo en `scripts/resultados_saga_abet6.txt`.

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
- [ADR-005 · Coordinación de transacciones distribuidas mediante patrón Saga e idempotencia](docs/adr/ADR-005-transacciones-distribuidas-saga.md)

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
