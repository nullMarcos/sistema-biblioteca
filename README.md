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
- **`catalogo_service` (Microservicio Interno gRPC)**: Desarrollado sobre **gRPC**, **SQLAlchemy 2.0** y SQLite (`catalogo.db`). Proporciona disponibilidad, catálogo con filtros, reserva y liberación. Las escrituras adquieren el escritor de SQLite con `BEGIN IMMEDIATE` antes de seleccionar el ejemplar; no se atribuye bloqueo de filas a SQLite. La espera está acotada por `SQLITE_BUSY_TIMEOUT` (0,5 s por defecto); la saturación de escritura devuelve gRPC `UNAVAILABLE`. Incluye apagado ordenado (*graceful shutdown*).
- **`sanciones_service` (Microservicio REST de Verificación y Resiliencia)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Administra bloqueos activos y multas pendientes en una base SQLite propia (`sanciones.db`). Sus controles internos de caos inducen latencia/fallas. La Saga usa el consumidor de `prestamos_service/src/sanciones_client.py`: deadline total de 1,5 s, Circuit Breaker con una sola sonda HALF_OPEN, Bulkhead de cinco llamadas por proceso y fallback conservador a `EN_REVISION_MANUAL`, persistido como `EN_REVISION`.

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
├── .env.example                    # Credenciales internas, puerto y políticas configurables
├── README.md                       # Documentación principal, ejecución y pruebas
├── contracts/                      # Contratos compartidos entre consumidores y servicios
│   ├── openapi.yaml                # OpenAPI 3.1.0 de Préstamos interno
│   ├── gateway.openapi.yaml        # Contrato público JWT /api/v1
│   ├── sanciones.openapi.yaml      # Contrato interno de Sanciones y caos
│   └── catalogo.proto              # Contrato de servicio y mensajes Protobuf/gRPC v3
├── docs/
│   └── experimentos/                # Experimento ABET 6 y artefactos generados
│       ├── experimento.py           # Script de medición JSON/Protobuf, con y sin gzip
│       ├── requirements.txt         # Dependencias locales del experimento
│       ├── README.md                # Método, hipótesis y análisis experimental
│       ├── resultados.csv           # Mediciones crudas
│       ├── resultados_resumen.csv   # Resumen estadístico
│       └── resultados.png           # Gráfico comparativo generado
├── scripts/
│   ├── test_resilience_sanciones.py # Entrada al perfil aislado de resiliencia efectiva
│   └── test_saga_escenarios.py      # Escenarios reales por Gateway en Compose desechable
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

- [`contracts/openapi.yaml`](contracts/openapi.yaml): OpenAPI 3.1.0 de Préstamos interno (`/v1`, `X-API-Key`, consultas de Saga/devolución y diagnóstico).
- [`contracts/gateway.openapi.yaml`](contracts/gateway.openapi.yaml): operaciones públicas equivalentes bajo `/api/v1`, JWT, login, errores y rate limiting. Los enlaces HATEOAS del servicio conservan rutas internas `/v1`.
- [`contracts/sanciones.openapi.yaml`](contracts/sanciones.openapi.yaml): contrato interno de Sanciones, su health y controles de caos; no se publican por Gateway.
- [`contracts/catalogo.proto`](contracts/catalogo.proto): contrato Protobuf v3 del servicio y los mensajes gRPC.

Los stubs versionados están en `catalogo_service/protos/` y `prestamos_service/protos/`. El mismo generador se usa en Docker y desarrollo. Preparar el entorno desde la raíz (Python 3.13 para las comprobaciones locales de este bloque):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/generar_protos.py
.venv/bin/python scripts/generar_protos.py --check
.venv/bin/python scripts/generar_openapi.py --check
```

Los contratos REST se exportan con `.venv/bin/python scripts/generar_openapi.py` desde las aplicaciones FastAPI en procesos separados y sin siembras. El check detecta deriva de rutas, modelos y respuestas. Se usa OpenAPI 3.1.0 para conservar los schemas nativos de FastAPI (incluidos campos nulos), en lugar de convertirlos manualmente a 3.0. Las pruebas validan respuestas reales contra esos schemas.
Las fechas de salida se normalizan a UTC y se validan como RFC 3339. Las fechas naive de SQLite se interpretan como UTC, que es la zona usada por `CURRENT_TIMESTAMP` y las semillas; esta normalización del DTO no modifica registros históricos.

El check falla si los archivos versionados difieren del compilador fijado; no los corrige silenciosamente. Docker genera sus propios stubs sin copiarlos desde el host. Catálogo implementa `ConsultarDisponibilidad`, `ReservarEjemplar`, `LiberarEjemplar`, `ListarCatalogo`, `ObtenerLibro`, `ConsultarOperacion` y `CancelarReserva`.
Todas estas operaciones son unarias: una solicitud produce una respuesta. Se conserva `package catalogo` y sus números de campo; migrar a `catalogo.v1` requeriría una interfaz compatible explícita.

### Identidad, cupos y devolución

- El package gRPC sigue siendo `catalogo` y se conservan los números de campos previos. Las escrituras requieren `operacion_id`; `LiberarEjemplar` exige también `reserva_id`. Los consumidores antiguos sin identidad reciben `INVALID_ARGUMENT`; las lecturas existentes conservan su interfaz.
- Catálogo confirma inventario y operación juntos. El replay conserva la asignación original y expone el estado actual: Préstamos solo consolida `RESERVADA`. Una reserva terminal no asigna otra copia. `ALREADY_EXISTS` indica payload incompatible; `FAILED_PRECONDITION`, propiedad o inventario que requiere conciliación. Una cancelación anterior a la reserva deja una tumba que impide su ejecución tardía.
- `POST /v1/prestamos` admite `idempotency_key` de 1–64 caracteres con patrón `[A-Za-z0-9][A-Za-z0-9._:-]*`. Se liga al socio, libro, opciones y actor interno. Si se omite, se genera y se devuelve. Los resultados terminales se repiten; los pendientes y payloads incompatibles producen conflicto explícito.
- Préstamos reserva el cupo en SQLite antes de llamar a Catálogo. `MAX_PRESTAMOS_POR_SOCIO=5` es una decisión configurable del proyecto. ACTIVO, EN_REVISION y reservas/devoluciones pendientes ocupan cupo; un socio no puede duplicar el mismo libro. RUT/correo duplicados devuelven 409, también bajo concurrencia.
- La acción principal es `POST /v1/prestamos/{id}/devolucion`, con header opcional `Idempotency-Key` del mismo formato. ACTIVO pasa a `DEVOLUCION_PENDIENTE` y luego `DEVUELTO`. Para EN_REVISION, la misma acción cancela a `ANULADO`, sin aprobar ni entregar el ejemplar. DELETE es un alias obsoleto del mismo caso de uso. Con la misma clave se repite el resultado; una petición nueva sin clave sobre DEVUELTO devuelve 409.
- El progreso se consulta internamente en `GET /v1/prestamos/saga/idempotency/{clave}` y `GET /v1/prestamos/devoluciones/idempotency/{clave}`. HATEOAS ofrece POST para devolver ACTIVO o cancelar EN_REVISION; las operaciones pendientes y los estados terminales no ofrecen esas acciones.
- `simular_fallo_paso3=true` solo se acepta con `HABILITAR_FALLOS_DEMO=true` y actor administrador (JWT admin validado por Gateway, que reemplaza las cabeceras de identidad). Sin habilitación/autorización devuelve 403 antes de iniciar una Saga. Por defecto está deshabilitado. Los errores esperados conservan `{codigo, mensaje}`: 502 respuesta/protocolo inválido, 503 indisponibilidad/circuito abierto y 504 espera agotada; un fallo real de BD continúa siendo 500.

### Recuperación interna de operaciones

Préstamos confirma intención/identidad antes del efecto remoto y préstamo/resultado/COMPLETADA en una sola transacción local. Las compensaciones se reconstruyen desde BD: primero anulación local si hubo préstamo confirmado, paso de Sanciones de solo lectura, después cancelación de la reserva propietaria. Un fallo conserva `COMPENSACION_PENDIENTE` y su cupo. La devolución conserva `DEVOLUCION_PENDIENTE` hasta cerrar el registro local.

El runner interno procesa un lote acotado de Sagas y devoluciones. Una Saga interrumpida se cancela por su identidad original; una devolución se finaliza usando la misma liberación. Se invoca dentro del servicio que posee esos datos:

```bash
docker compose exec -T prestamos python -m src.saga.recuperacion --limite 10
```

Emite JSON con procesadas, resueltas, pendientes, acciones e intentos; termina con código 2 si quedan pendientes y 0 si no quedan. Tiene backoff de 1 a 60 s y máximo de 5 intentos por operación, configurables con `SAGA_RECUPERACION_*` en `.env.example`. Alcanzar el máximo deja el trabajo auditable en pausa. Para reanudar una operación concreta después de resolver su causa, agregar `--reanudar --saga-id ID` o `--reanudar --devolucion-id ID`, usando el identificador consultado; conserva sus identidades remotas.

La adquisición y renovación del lease se persisten en SQLite. `SAGA_LEASE_SECONDS` vale 30 s y debe superar el presupuesto de una llamada remota más margen local; se valida esa relación. Un lease vigente excluye al recuperador y un worker vencido no puede confirmar. El runner se ejecuta por comando operativo; no hay una tarea periódica instalada.

### Resiliencia efectiva y tiempos

La respuesta de Sanciones se valida antes de habilitar un préstamo; identidad distinta, datos inválidos o socio remoto desconocido producen revisión/error, nunca ausencia supuesta de sanciones. En fallback, bloqueo, conteos e importe desconocidos son `null`.

Por defecto: gRPC 0,6 s por intento y un retry con backoff 0,1 s (1,3 s por RPC), Sanciones 1,5 s total, Saga/devolución 8 s y Gateway 10 s. Los pasos comparten deadline, y el presupuesto reserva margen para compensación y persistencia local. Las variables están en `.env.example`; se valida su coherencia. Las duraciones e intentos reales se registran en logs y en `/internal/resiliencia` de Préstamos, protegido por API Key y sin ruta pública. Los límites CB/Bulkhead son por proceso.

### Pruebas aisladas y preparación de migraciones

```bash
.venv/bin/python scripts/test_unitarios.py
.venv/bin/python -m pip check
.venv/bin/python scripts/verificar_compose.py
.venv/bin/python scripts/verificar_operaciones.py
```

El primer runner comprueba deriva y ejecuta, en procesos separados, las suites comunes, de Catálogo, Préstamos, Sanciones y Gateway. Usan SQLite temporal, RPC locales, clientes falsos unitarios y procesos reales con puertos propios. Incluyen concurrencia, respuesta perdida, fallos de persistencia y muerte/reinicio de procesos; sus semillas se ejecutan únicamente sobre archivos temporales.

El runner de Compose usa explícitamente `docker-compose.yml`, sin overrides ni `.env` locales, y fija la URL interna de Sanciones. Crea dos proyectos con nombres/puertos propios, comprueba bindings mediante `docker inspect`, DNS, una lectura gRPC real, separación de volúmenes/datos y arranque de Préstamos con Sanciones detenida. Su limpieza elimina los contenedores, redes, volúmenes e imágenes de esos proyectos desechables.

`verificar_operaciones.py` usa otro proyecto desechable: prueba una Saga con Catálogo y Sanciones reales por Gateway, replay/colisión de payload, devolución POST/DELETE, fallo real de SQLite con compensación y recreación conservando datos/migraciones. Termina con error ante cualquier aserción fallida y limpia los recursos de su ejecución.

Para la regresión completa de la fase A:

```bash
.venv/bin/python scripts/test_saga_escenarios.py
# Perfil que comprueba solo resiliencia del consumidor efectivo:
.venv/bin/python scripts/test_resilience_sanciones.py
```

Ambos construyen sin caché por defecto, usan proyecto/puerto/volúmenes propios y generan JSON, logs y snapshots SQLite en una carpeta temporal indicada al finalizar. `--salida CARPETA_NUEVA` permite elegirla; `--usar-cache` reutiliza capas para una repetición de desarrollo. El runner completo añade stock válido, sanción, máximo configurable, clave concurrente, fallos reales de BD, caída/pausa de Catálogo, recuperación, timeout/CB/Bulkhead de Sanciones y conservación al recrear los cuatro servicios. Ningún resultado se anuncia si no fue comprobado.

`bash scripts/test_gateway.sh --proyecto NOMBRE --base-url http://127.0.0.1:PUERTO` comprueba un proyecto existente. Acepta también `COMPOSE_PROJECT_NAME`, `BASE_URL`/`GATEWAY_PORT` y `--reporte ARCHIVO`. Verifica bindings efectivos/configurados y sondas TCP (incluido gRPC), y obtiene la política de tasa del contenedor. Espera dos ventanas para medirla; termina con error ante incumplimientos. Un listener ajeno en 8000 no se atribuye a este proyecto solo por estar abierto.

Catálogo y Préstamos aplican al arrancar sus migraciones SQL de `migrations/` mediante `scripts/migrar_sqlite.py`: registran checksum, se aplican una sola vez y hacen rollback si fallan. `create_all()` completa la inicialización; no migra columnas existentes. Las migraciones conservan datos anteriores y no inventan identidades para reservas legacy: esos préstamos requieren conciliación explícita de su propiedad antes de devolverlos.

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

El arranque siembra solo bases vacías. Conserva 29 títulos, 66 copias disponibles y tres socios correlacionados por RUT; dos libres y uno sancionado. Los préstamos de demostración se crean por la Saga real, sin asumir IDs entre bases. Una segunda ejecución no duplica ni modifica datos. También puede invocarse cada semilla directamente:

```bash
python catalogo_service/src/seed.py
API_KEY=biblioteca_dev_key_2026 python prestamos_service/src/seed.py
python sanciones_service/src/seed.py
```

`--reset` rechaza bases pobladas. Para diagnosticar datos anteriores se usa `scripts/diagnosticar_consistencia.py` sobre copias estables de los tres almacenes; consulta en solo lectura, enumera discrepancias por IDs y devuelve código 2 si encuentra alguna.

### Funcionalidades y Patrones Implementados

- **Microservicio Sanciones y Resiliencia (Integrante 3):**
  - **Identificador de Negocio Compartido (`rut`):** Correlación de socios entre `prestamos_service` y `sanciones_service` mediante clave natural única (ej. `11.111.111-1`), desacoplando IDs autonuméricos locales.
  - **Módulo de Caos en Vivo:** Endpoints `/chaos/delay?seconds=X` y `/chaos/fault?status=500` para inducir lentitud y fallas controladas, además de `/chaos/reset` y `/chaos/status`.
  - **Timeout:** Tiempo límite estricto de 1.5s en llamadas remotas hacia Sanciones, conteniendo la lentitud artificial.
  - **Circuit Breaker efectivo:** Máquina de 3 estados en `prestamos_service/src/circuit_breaker.py`. Tras tres fallos consecutivos transiciona a OPEN y evita la red; la recuperación admite una sonda. Su latencia se mide en cada ejecución.
  - **Bulkhead efectivo:** Control por proceso en `prestamos_service/src/sanciones_client.py`, cinco llamadas simultáneas por defecto, con rechazo de saturación y cupos retenidos hasta terminar el transporte.
  - **Fallback Elegante:** Regla de negocio que no rechaza el préstamo a ciegas ante caídas o timeouts, degradando la respuesta a `ESTADO: EN_REVISION_MANUAL`.
  - **Script de Demostración del Escenario 3:**
    ```bash
    python scripts/test_resilience_sanciones.py
    ```
    Ejecuta el perfil Compose del consumidor real: timeout, circuito abierto sin llamada de red, revisión, recuperación, Bulkhead y una lectura útil mientras Sanciones está lenta. El antiguo cliente aislado se conserva en `sanciones_service/src/client.py`; ya no se usa como sustituto de esa evidencia.

- **Transacción Distribuida con Patrón Saga (Orquestador y Compensaciones):**
  - **Coordinación Centralizada:** Implementación del patrón Saga mediante orquestación (`PrestamoSagaOrchestrator`) para la operación de préstamo, coordinando Catálogo (gRPC), Sanciones (HTTP resiliente) y Préstamos (DB local).
  - **Compensación Semántica en Orden Inverso:** Progreso y acción pendiente persistidos. Primero se anula un préstamo parcial si existe; Sanciones es solo lectura; después se cancela por identidad en Catálogo. Solo se marca COMPENSADA al confirmar todas las acciones; los fallos quedan pendientes y recuperables.
  - **Idempotencia Estricta:** Manejo de llaves únicas `idempotency_key` con tabla de persistencia `saga_transacciones`, retornando resultados precalculados ante reintentos sin duplicar operaciones ni reservas.
  - **Degradación Elegante:** Integración con la regla de negocio de Sanciones; si el verificador no responde o está en fallback, el préstamo se registra con estado `EN_REVISION`.
  - **Script de Demostración de Escenarios 1 y 2 (ABET 6):**
    ```bash
    python scripts/test_saga_escenarios.py
    ```
    Ejecuta los escenarios reales por Gateway y genera `resultado.json` con peticiones, duraciones, configuración y estados comprobados, además de logs y diagnóstico. El fallo controlado se habilita solo en el proyecto desechable y requiere JWT de administrador. También se prueba un fallo real de SQLite.

- **Métodos Core gRPC (Catálogo):**
  - `ReservarEjemplar(ReservaRequest)`: Reserva atómica de ejemplar disponible (`DISPONIBLE` -> `PRESTADO`).
  - `LiberarEjemplar(LiberarRequest)`: Liberación de ejemplar prestado (`PRESTADO` -> `DISPONIBLE`).
  - `ConsultarDisponibilidad(ConsultaRequest)`: Consulta de conteo total y disponibles por libro.
  - `ListarCatalogo(CatalogoRequest)`: Lista el catálogo con filtros opcionales de género y disponibilidad.
  - `ObtenerLibro(LibroRequest)`: Obtiene la información detallada de un libro y sus ejemplares.
  - `ConsultarOperacion(OperacionRequest)`: Consulta la asignación y estado por identidad persistente.
  - `CancelarReserva(CancelacionRequest)`: Cancela por identidad, incluso antes de recibir la reserva original.
- **HATEOAS (Préstamos):** Acciones POST de devolución para ACTIVO y cancelación para EN_REVISION, según su estado.
- **Resiliencia Catálogo:** Circuit Breaker y política de reintentos con backoff exponencial sobre llamadas gRPC hacia Catálogo.
- **Manejo Estandarizado de Errores `{codigo, mensaje}`:** Respuestas estandarizadas en todas las APIs bajo el contrato OpenAPI.
- **Health Checks:** Endpoints `/health` dedicados en Préstamos y Sanciones.
- **Concurrencia SQLite:** Transacciones breves con `BEGIN IMMEDIATE` en reserva y liberación, sin llamadas de red dentro de la transacción. Se comprueban 20 reservas concurrentes sobre dos copias en bases desechables.
- **Apagado Ordenado (Graceful Shutdown):** Manejo de señales `SIGTERM` y `SIGINT` en los servicios.

## Experimento ABET 6

Compara el tamaño de un `LibroDTO` serializado como JSON UTF-8 y Protobuf, con y sin gzip, para varios números de ejemplares. Desde la raíz:

```bash
python3 -m pip install -r docs/experimentos/requirements.txt
python3 docs/experimentos/experimento.py
```

Genera [`resultados.csv`](docs/experimentos/resultados.csv), [`resultados_resumen.csv`](docs/experimentos/resultados_resumen.csv) y [`resultados.png`](docs/experimentos/resultados.png). El método, entorno, hipótesis e interpretación están en [`docs/experimentos/README.md`](docs/experimentos/README.md); el script está en [`docs/experimentos/experimento.py`](docs/experimentos/experimento.py).

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
- **Decisiones Arquitectónicas Propias:** Las decisiones de diseño fundamentales incluyen aislamiento de bases mediante *Database-per-Service*, integración síncrona REST/gRPC, transacciones compatibles con SQLite y dimensionamiento de umbrales del disyuntor. Sus garantías se contrastan con las pruebas de cada bloque de corrección.
- **Verificación Práctica:** La corrección y fiabilidad del software fueron validadas mediante la ejecución exitosa de la suite de pruebas unitarias y el despliegue reproducible en contenedores Docker.
