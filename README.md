# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción

Sistema distribuido de gestión de biblioteca basado en una arquitectura desacoplada de microservicios, desarrollado en el marco de la asignatura de **Integración de Sistemas** (Universidad de Concepción). El sistema resuelve la administración de socios, catálogo bibliográfico, inventario físico de ejemplares, verificación de bloqueos y multas, y el ciclo de vida completo de préstamos y devoluciones de libros.

La solución tiene tres servicios de dominio con persistencia independiente, un BFF web y un Gateway como entrada pública:

- **`prestamos_service` (API REST interna)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Atiende `/v1/socios` y `/v1/prestamos`, protegidos con `X-API-Key` e identidad técnica. Gestiona `prestamos.db`, reglas de negocio, HATEOAS y la Saga durable. Consume Catálogo por gRPC y Sanciones por HTTP. Gateway publica sus operaciones bajo `/api/v1` con JWT y autorización por socio.
- **`catalogo_service` (Microservicio interno gRPC)**: Desarrollado sobre **gRPC**, **SQLAlchemy 2.0** y SQLite (`catalogo.db`). Proporciona disponibilidad, catálogo con filtros, reserva y liberación. Las escrituras adquieren el escritor de SQLite con `BEGIN IMMEDIATE` antes de seleccionar el ejemplar; no se atribuye bloqueo de filas a SQLite. La espera está acotada por `SQLITE_BUSY_TIMEOUT` (0,5 s por defecto); la saturación devuelve gRPC `UNAVAILABLE`. Incluye apagado ordenado.
- **`sanciones_service` (Microservicio REST de verificación)**: Desarrollado con **FastAPI** y **SQLAlchemy 2.0**. Administra bloqueos y multas en `sanciones.db`; sus controles internos de caos inducen latencia/fallas. La Saga usa `prestamos_service/src/sanciones_client.py`: deadline total de 1,5 s, Circuit Breaker con una sola sonda HALF_OPEN, Bulkhead de cinco llamadas por proceso y fallback conservador a `EN_REVISION_MANUAL`, persistido como `EN_REVISION`.
- **`bff_service` (composición para web)**: FastAPI sin base de datos. Agrega socio/préstamos por HTTP, títulos/autores por gRPC y sanciones por RUT mediante HTTP. Tiene DTO propios, clientes asíncronos acotados y resiliencia independiente. El POST delega una sola escritura a la Saga existente con `Idempotency-Key` obligatoria.

## Arquitectura

- **`gateway_service`**: única entrada publicada, por defecto `http://localhost:8080`. Autentica JWT, limita peticiones y enruta `/api/v1/socios`, `/api/v1/prestamos` y `/bff/web/*`.
- **`bff_service`**: FastAPI/REST en `bff:8000`; sin puertos publicados, volúmenes ni acceso a archivos SQLite ajenos.
- **`prestamos_service`**: FastAPI/REST en `prestamos:8000`, accesible dentro de Compose y protegido con API Key; persistencia propia en `prestamos.db`.
- **`catalogo_service`**: Servidor gRPC en el puerto `50051`; persistencia propia en `catalogo.db`.
- **`sanciones_service`**: FastAPI/REST en `sanciones:8000`, sin puerto publicado al host; persistencia propia en `sanciones.db`.
- **Patrón Database-per-Service**: Cada microservicio administra su propio almacenamiento SQLite de forma exclusiva (3 bases de datos independientes: `prestamos.db`, `catalogo.db`, `sanciones.db` montadas en volúmenes Docker). No existen llaves foráneas directas (`ForeignKey`) ni cruces entre bases de datos; la correlación se efectúa mediante identificadores lógicos (`libro_id`, `ejemplar_id`) y una clave natural de negocio compartida (**`rut`**) para la identidad de socios sin acoplar secuencias numéricas.
- **Comunicación y red**: Compose crea `<proyecto>_biblioteca_net` y nombres de contenedor propios del proyecto. BFF y Préstamos consumen `catalogo:50051` por gRPC y `sanciones:8000` por HTTP REST. Las tres bases usan volúmenes independientes con prefijo del proyecto.

```text
GET web → Gateway (JWT/tasa) → BFF → Préstamos HTTP: socio + préstamos
                                  ├→ Catálogo gRPC: libros distintos
                                  └→ Sanciones HTTP: RUT del socio

POST web + Idempotency-Key → Gateway → BFF → Préstamos/Saga
                                             ├→ Catálogo gRPC: reserva durable
                                             ├→ Sanciones HTTP: verificación
                                             └→ SQLite local: préstamo y resultado
```

## Estructura del repositorio

```text
sistema-biblioteca/
├── docker-compose.yml              # Orquestación de servicios, puertos, red y volúmenes
├── .env.example                    # Credenciales internas, puerto y límites configurables
├── README.md                       # Documentación principal, ejecución y pruebas
├── contracts/                      # Contratos compartidos entre consumidores y servicios
│   ├── openapi.yaml                # OpenAPI 3.1.0 de Préstamos interno
│   ├── gateway.openapi.yaml        # Contrato público JWT /api/v1 y /bff/web
│   ├── bff.openapi.yaml            # Las dos operaciones públicas del BFF
│   ├── sanciones.openapi.yaml      # Contrato interno de Sanciones y caos
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
│   ├── test_resilience_sanciones.py # Perfil aislado del consumidor efectivo de Saga
│   ├── test_saga_escenarios.py      # Regresiones reales por Gateway en Compose desechable
│   └── verificar_bff.py             # Contrato y recorridos BFF reales por Gateway
├── gateway_service/                # JWT, tasa y proxy de entrada pública
├── bff_service/                    # Composición web sin base de datos
│   ├── main.py                     # FastAPI, lifespan, correlación y errores
│   ├── protos/                     # Stubs generados desde el contrato común
│   ├── tests/                      # API, contratos, transportes y concurrencia
│   └── src/                        # Configuración, DTO, autorización y clientes async
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

Desde la raíz del repositorio, revisar la configuración y levantar los cinco servicios:

```bash
docker compose config
docker compose up --build -d --wait
```
Compose construye Catálogo, Préstamos, Sanciones, BFF y Gateway. En el primer inicio se crean y siembran las bases vacías. Los volúmenes conservan datos entre reinicios. El único binding al host es `${GATEWAY_PORT:-8080}:8080`; se configura en `.env` junto con `API_KEY` y `JWT_SECRET` (ver `.env.example`).

Los builds usan la raíz como contexto. Los `Dockerfile.dockerignore` limitan cada contexto al código del servicio y sus entradas de build: no incluyen `.env`, BD, pruebas ni stubs preexistentes. Las imágenes base fijan parche/digest y `requirements/constraints.txt` fija dependencias transitivas. Los servicios de dominio y BFF usan Python 3.14; Gateway usa 3.12.

Los healthchecks comprueban disponibilidad del servidor. Préstamos usa `/openapi.json` como liveness; `/health` conserva su diagnóstico de dependencias y puede indicar degradación. BFF usa `/health`, sin llamar a otros servicios. Préstamos espera a Catálogo; Gateway espera a Préstamos y BFF. BFF puede arrancar con fuentes caídas, y Préstamos puede arrancar sin Sanciones. `docker compose up --wait` del proyecto completo sí espera los cinco contenedores.

Para dos instalaciones independientes, usar proyecto y puerto distintos, por ejemplo `GATEWAY_PORT=8081 docker compose -p biblioteca-otra up --build -d --wait`. No se renombran ni borran automáticamente volúmenes existentes.

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

El cliente externo obtiene un JWT en `POST /auth/login` de Gateway y envía `Authorization: Bearer TOKEN`. Las cuentas de demostración son `admin/admin123` y `socio1/socio123`. Gateway usa `X-API-Key` como credencial interna hacia BFF/Préstamos; el cliente web no la necesita. En Compose, `API_KEY` usa `biblioteca_dev_key_2026` si no está definida. Configurar valores propios creando `.env` desde `.env.example`:

```bash
cp .env.example .env
# Editar API_KEY, JWT_SECRET y GATEWAY_PORT en .env
```

Las credenciales anteriores son de demostración. La cuenta `socio1` tiene `sub="2"` (cuenta) y `socio_id=SOCIO1_SOCIO_ID` (socio real de Préstamos, por defecto 1). Son identidades distintas. El administrador puede atender socios; un socio solo consulta/solicita/devuelve sus propios recursos. Préstamos aplica esa restricción también en las rutas alternativas `/api/v1`, incluidos listados y consultas de operaciones.

Gateway elimina las cabeceras entrantes `X-User-*`, `X-Socio-*` y `X-API-Key`, y las reemplaza con identidad validada. Un JWT de socio sin vínculo válido recibe 401; un recurso ajeno recibe 403. BFF verifica credencial interna e identidad, sin otro login. Los diagnósticos y controles de caos se consultan dentro de Compose.

## URLs del Sistema

| Servicio / Recurso | Dirección | Autenticación | Descripción |
|---|---|---|---|
| **Health Gateway** | <http://localhost:8080/health> | Pública | Liveness del Gateway |
| **Login** | <http://localhost:8080/auth/login> | Credenciales demo | POST que entrega JWT |
| **Swagger Gateway** | <http://localhost:8080/docs> | Página pública | Esquema del proxy y login; contratos detallados en `contracts/` |
| **Socios** | <http://localhost:8080/api/v1/socios> | JWT | Alta/listado y consulta autorizada por socio |
| **Préstamos** | <http://localhost:8080/api/v1/prestamos> | JWT | Registro, listado, devolución y consultas de operaciones |
| **Resumen web** | <http://localhost:8080/bff/web/resumen-prestamos-socio/1> | JWT | GET agregado para el socio 1 |
| **Solicitud web** | <http://localhost:8080/bff/web/solicitar-prestamo> | JWT + `Idempotency-Key` | POST delegado a la Saga |

Sustituir el puerto por `GATEWAY_PORT` y el ID por el socio real. Catálogo, Préstamos, Sanciones y BFF solo tienen DNS interno. Ejemplo de diagnóstico:

```bash
docker compose exec -T sanciones python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health', timeout=3).read().decode())"
```

### Cómo probar los endpoints protegidos

Desde la terminal, obtener un JWT y consultar los socios para elegir un ID real. Los IDs 1 usados abajo corresponden a una instalación recién sembrada; ajustar también el libro a uno disponible:

```bash
BASE_URL=http://localhost:8080
TOKEN=$(curl -fsS -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' "$BASE_URL/auth/login" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/v1/socios"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/bff/web/resumen-prestamos-socio/1"
curl -fsS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: solicitud-web-001' -H 'X-Request-Id: demo-web-001' \
  -d '{"socio_id":1,"libro_id":1}' "$BASE_URL/bff/web/solicitar-prestamo"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/bff/web/resumen-prestamos-socio/1"
```

Repetir el mismo POST con `solicitud-web-001` conserva la operación; usar esa misma clave con otro socio/libro produce 409. Ante un timeout, repetir con la misma clave y actor: la ausencia de respuesta no acredita una reversión. `X-Request-Id` identifica cada petición y es independiente de la clave durable de escritura.
---

## Contratos

- [`contracts/openapi.yaml`](contracts/openapi.yaml): OpenAPI 3.1.0 de Préstamos interno (`/v1`, API Key, Saga/devolución y diagnóstico).
- [`contracts/gateway.openapi.yaml`](contracts/gateway.openapi.yaml): operaciones públicas `/api/v1` y `/bff/web`, JWT, login, errores y rate limiting.
- [`contracts/bff.openapi.yaml`](contracts/bff.openapi.yaml): las dos operaciones públicas web, DTO propios, idempotencia, errores y campos nulos.
- [`contracts/sanciones.openapi.yaml`](contracts/sanciones.openapi.yaml): interfaz interna de Sanciones, health y caos; no se publica por Gateway.
- [`contracts/catalogo.proto`](contracts/catalogo.proto): contrato Protobuf v3 del servicio y los mensajes gRPC.

Los stubs versionados están en `catalogo_service/protos/`, `prestamos_service/protos/` y `bff_service/protos/`. El mismo generador se usa en Docker y desarrollo. Desde la raíz (Python 3.13 para estas comprobaciones locales):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/generar_protos.py
.venv/bin/python scripts/generar_protos.py --check
.venv/bin/python scripts/generar_openapi.py --check
```

Los contratos REST se exportan con `.venv/bin/python scripts/generar_openapi.py` desde FastAPI, en procesos separados y sin siembras. Los checks detectan deriva sin corregirla silenciosamente. OpenAPI 3.1.0 conserva los schemas nativos, incluidos campos nulos. Las pruebas validan respuestas reales y fechas RFC 3339. Las fechas naive de SQLite se interpretan como UTC en el DTO, sin modificar filas históricas.

Docker genera stubs sin copiarlos desde el host. Catálogo implementa `ConsultarDisponibilidad`, `ReservarEjemplar`, `LiberarEjemplar`, `ListarCatalogo`, `ObtenerLibro`, `ConsultarOperacion` y `CancelarReserva`. Todas son operaciones unarias. Se conserva `package catalogo` y sus números de campo; migrar a `catalogo.v1` requeriría una interfaz compatible explícita.

### Comportamiento del BFF web

El resumen toma `socio.id/nombre` y préstamos anidados desde Préstamos; consulta una vez cada libro distinto a Catálogo para `titulo/autor`, y usa el RUT de Préstamos para verificar Sanciones. Ordena por ID y separa `prestamos_activos`, `prestamos_en_revision` y `prestamos_pendientes` de devolución. Devueltos/anulados no aparecen como activos. No devuelve correo, RUT ni enlaces HATEOAS internos en su DTO.

| Situación | Respuesta |
|---|---|
| Tres fuentes disponibles | 200, `parcial=false`, datos reales |
| Socio sin préstamos | Listas vacías; consulta Sanciones y omite Catálogo |
| Socio inexistente | 404 |
| Préstamos caído/lento/esquema inválido | 503/504/502; no se inventa un resumen |
| Sanciones caído/lento/circuito abierto/saturado | 200 parcial, verificación `DESCONOCIDO` y valores desconocidos `null` |
| RUT no registrado en Sanciones | 200 parcial, `NO_REGISTRADO`; no confirma habilitación |
| Catálogo inaccesible o libro ausente | Conserva IDs/estado, título/autor `null` y advertencia por libro |
| POST confirmado | 201 con estado real; `EN_REVISION` no aprueba ni autoriza entrega |
| POST sin resultado confirmado | Error explícito; consultar/repetir con la misma clave |

El POST solo admite `socio_id` y `libro_id` positivos; rechaza campos extra, incluidos controles de caos. Exige `Idempotency-Key` de 1–64 caracteres (`[A-Za-z0-9][A-Za-z0-9._:-]*`). No consulta el resumen para aprobar, no reserva ni compensa: transmite clave y actor a Préstamos. BFF y Gateway no reintentan automáticamente las escrituras. El resumen no representa una transacción ni un snapshot atómico entre los tres almacenes.

### Configuración y resiliencia del BFF

Todas las variables están en `.env.example`. Los límites son por proceso, con admisión inmediata y sin cola de tareas por préstamo:

| Variable | Valor por defecto / función |
|---|---|
| `BFF_PRESTAMOS_URL` / `BFF_SANCIONES_URL` | `http://prestamos:8000` / `http://sanciones:8000` |
| `BFF_CATALOGO_TARGET` | `catalogo:50051` |
| `BFF_RESUMEN_TIMEOUT_SEGS` / `BFF_POST_TIMEOUT_SEGS` | 3 s total GET / 9 s POST |
| `BFF_PRESTAMOS_TIMEOUT_SEGS` | 0,8 s para leer el socio |
| `BFF_SANCIONES_TIMEOUT_SEGS` / `BFF_CATALOGO_TIMEOUT_SEGS` | 1,5 s / 0,6 s por llamada, limitados además por el deadline restante |
| `BFF_MAX_PETICIONES` | 32 operaciones BFF admitidas |
| `BFF_PRESTAMOS_MAX_CONCURRENT` / `BFF_SANCIONES_MAX_CONCURRENT` / `BFF_CATALOGO_MAX_CONCURRENT` | 16 / 5 / 8 llamadas por dependencia |
| `BFF_CATALOGO_POR_RESUMEN` | 4 trabajadores por resumen; deduplicación sin caché persistente |
| `BFF_SANCIONES_CB_THRESHOLD` / `BFF_SANCIONES_CB_RECOVERY_SEGS` | 3 fallos / 5 s hasta permitir una sonda HALF_OPEN |
| `BFF_MAX_RESPUESTA_BYTES` | 2097152 para Préstamos/gRPC; Sanciones se limita a 65536 bytes |
| `SOCIO1_SOCIO_ID` | 1; vínculo explícito de la cuenta demo con Préstamos |

Se reutilizan conexiones HTTP y canal gRPC y se cierran al apagar. Cada cuerpo HTTP tiene deadline total, incluso si llega por partes. Los slots se retienen hasta que termina/cierra el transporte. El circuito y Bulkhead de Sanciones son propios del BFF. Por defecto, BFF permite 5 y Préstamos permite 5: la suma configurada es 10 por par de procesos, no un límite global de 5 para Sanciones.

`/internal/metricas` del BFF requiere API Key y no tiene ruta pública. Expone estado del circuito, llamadas de red, fallos/fallbacks, concurrencia activa/pico/rechazos y última latencia. Los logs usan servicio, operación, `request_id`, resultado y duración, sin JWT, API Key ni respuestas personales completas. El proxy conserva `Retry-After`, `Idempotency-Key` y correlación.

### Identidad de operaciones, cupos y devolución

- Las escrituras gRPC requieren `operacion_id`; `LiberarEjemplar` exige además `reserva_id`. Los consumidores antiguos sin identidad reciben `INVALID_ARGUMENT`; las lecturas conservan su interfaz.
- Catálogo confirma inventario y operación juntos. El replay conserva la asignación original y expone su estado actual: Préstamos solo consolida `RESERVADA`. Una reserva terminal no asigna otra copia; una cancelación anticipada deja una tumba contra ejecución tardía. `ALREADY_EXISTS` indica payload incompatible y `FAILED_PRECONDITION` una propiedad/inventario que requiere conciliación.
- La Saga liga la clave al socio, libro, opciones y actor. Repite resultados terminales; operaciones pendientes y payloads incompatibles producen conflicto explícito. La ruta interna admite omitir la clave y devuelve una generada; el BFF siempre la exige al cliente.
- Préstamos admite cupos transaccionalmente antes de llamar a Catálogo. `MAX_PRESTAMOS_POR_SOCIO=5` es una decisión configurable del proyecto. ACTIVO, EN_REVISION y reservas/devoluciones pendientes ocupan cupo; no se duplica socio/libro. RUT/correo duplicados devuelven 409, también bajo concurrencia.
- `POST /v1/prestamos/{id}/devolucion` es la acción principal, con `Idempotency-Key` opcional. ACTIVO pasa por `DEVOLUCION_PENDIENTE` a `DEVUELTO`; EN_REVISION se cancela a `ANULADO`. DELETE es el alias obsoleto. La misma clave repite el resultado; una petición nueva sin clave sobre DEVUELTO devuelve 409.
- Las consultas de progreso son `/v1/prestamos/saga/idempotency/{clave}` y `/v1/prestamos/devoluciones/idempotency/{clave}`; públicamente llevan prefijo `/api`. HATEOAS del servicio conserva `/v1` y ofrece POST de devolución/cancelación según estado; el BFF omite esos enlaces internos.
- `simular_fallo_paso3=true` solo se acepta en la ruta de Préstamos con `HABILITAR_FALLOS_DEMO=true` e identidad admin validada por Gateway; de otro modo se rechaza antes de la Saga. Está deshabilitado por defecto. Los errores conservan `{codigo, mensaje}`: 502 respuesta/protocolo inválido, 503 indisponibilidad/circuito abierto y 504 espera agotada. Un fallo real de BD sigue siendo 500.

### Recuperación interna de operaciones

Préstamos confirma intención/identidad antes del efecto remoto y préstamo/resultado/COMPLETADA en una transacción local. Las compensaciones se reconstruyen desde BD: anulación local si hubo préstamo confirmado, Sanciones de solo lectura y cancelación de la reserva propietaria. Un fallo conserva `COMPENSACION_PENDIENTE` y su cupo; la devolución conserva `DEVOLUCION_PENDIENTE` hasta cerrar el registro local.

El runner procesa un lote acotado. Una Saga interrumpida se cancela por su identidad original; una devolución finaliza usando la misma liberación:

```bash
docker compose exec -T prestamos python -m src.saga.recuperacion --limite 10
```

Emite JSON con procesadas, resueltas, pendientes, acciones e intentos; sale 2 si quedan pendientes y 0 si no. Tiene backoff de 1 a 60 s y máximo de 5 intentos, configurables con `SAGA_RECUPERACION_*`. Al máximo queda en pausa auditable. Tras resolver la causa, se reanuda una operación con `--reanudar --saga-id ID` o `--reanudar --devolucion-id ID`, conservando identidades remotas.

El lease se adquiere/renueva en SQLite. `SAGA_LEASE_SECONDS=30` supera el presupuesto remoto más margen local; se valida esa relación. Un lease vigente excluye al recuperador y un worker vencido no puede confirmar. Es un comando operativo, sin tarea periódica instalada.

### Presupuestos compartidos y pruebas aisladas

Por defecto: gRPC de la Saga 0,6 s/intento, un retry y backoff 0,1 s (1,3 s por RPC); Sanciones 1,5 s total; Saga/devolución 8 s; POST BFF 9 s y Gateway 10 s. Los pasos comparten deadline y reservan margen de compensación/persistencia. Gateway valida margen sobre los presupuestos de BFF/Saga. `/internal/resiliencia` de Préstamos expone métricas con API Key, sin ruta pública.

```bash
.venv/bin/python scripts/test_unitarios.py
.venv/bin/python -m pip check
.venv/bin/python scripts/verificar_compose.py
.venv/bin/python scripts/verificar_operaciones.py
.venv/bin/python scripts/verificar_bff.py
```

El primer runner comprueba deriva y ejecuta suites comunes y de los cinco servicios en procesos separados (cada uno tiene su paquete `src`). Usan SQLite temporal, transportes locales, falsos unitarios y procesos reales con puertos propios. Cubren concurrencia, respuesta perdida, fallos de persistencia y muerte/reinicio de procesos.

`verificar_compose.py` usa `docker-compose.yml`, sin overrides ni `.env` locales. Crea dos proyectos, comprueba bindings/DNS, lectura gRPC real, BFF sin montajes, separación de volúmenes/datos y arranque de Préstamos sin Sanciones. `verificar_operaciones.py` usa otro proyecto para Saga, replay/colisión, devolución POST/DELETE, fallo SQLite y recreación conservando datos/migraciones. Ambos limpian sus recursos desechables.

`verificar_bff.py` construye sin caché por defecto y comprueba login → resumen → solicitud → resumen, contratos reales, IDs de Sanciones distintos para el mismo RUT, autorización/spoofing, prefijos públicos sin escape hacia diagnósticos, replay/concurrencia/respuesta no leída, compensación real y fallas/recuperación de fuentes. Bajo carga mide límites independientes de 2 en BFF y 2 en Préstamos (suma configurada 4), rechazos y una lectura útil. Comprueba también 429 real compartido API/BFF y logs correlacionados sin credenciales. Genera `resultado.json`, logs y snapshots en una carpeta temporal e indica su ruta. Admite `--salida CARPETA_NUEVA`, `--base-sha SHA` y `--usar-cache`.

Para las regresiones de la fase A y el perfil de resiliencia del consumidor efectivo:

```bash
.venv/bin/python scripts/test_saga_escenarios.py
.venv/bin/python scripts/test_resilience_sanciones.py
```

Estos runners también usan proyectos/puertos/volúmenes propios, sin caché por defecto, con JSON/logs/snapshots y salida no cero ante fallos. El completo añade stock válido, sanción, máximo configurable, clave concurrente, fallos de BD, pausa/caída de Catálogo, recuperación y recreación conservando datos.

`bash scripts/test_gateway.sh --proyecto NOMBRE --base-url http://127.0.0.1:PUERTO` comprueba un proyecto existente. Acepta `COMPOSE_PROJECT_NAME`, `BASE_URL`/`GATEWAY_PORT` y `--reporte ARCHIVO`. Inspecciona bindings y sondas TCP, obtiene la política real de tasa y espera dos ventanas para medirla; falla ante incumplimientos. Un listener ajeno en 8000 no se atribuye a este proyecto solo por estar abierto.

Catálogo y Préstamos aplican migraciones SQL incrementales de `migrations/` con checksum, una sola vez y con rollback si fallan. `create_all()` completa inicialización, no migra columnas existentes. No se inventan identidades para reservas legacy: requieren conciliación explícita antes de devolverlas.

### Ejecución Local de los Microservicios (sin Docker)

Tras instalar dependencias y generar stubs, activar el entorno en cada terminal (`source .venv/bin/activate`). Ejecutar cada proceso en su propia terminal desde la raíz, usando los destinos locales explícitos:

```bash
# 1. Catálogo (gRPC en puerto 50051)
python catalogo_service/main.py

# 2. Préstamos (REST en puerto 8000)
API_KEY=biblioteca_dev_key_2026 CATALOGO_GRPC_HOST=127.0.0.1 SANCIONES_URL=http://127.0.0.1:8001 \
  python -m uvicorn main:app --app-dir prestamos_service --reload --port 8000

# 3. Sanciones (REST en puerto 8001)
python -m uvicorn main:app --app-dir sanciones_service --reload --port 8001

# 4. BFF (REST en puerto 8002)
API_KEY=biblioteca_dev_key_2026 BFF_PRESTAMOS_URL=http://127.0.0.1:8000 \
  BFF_SANCIONES_URL=http://127.0.0.1:8001 BFF_CATALOGO_TARGET=127.0.0.1:50051 \
  python -m uvicorn main:app --app-dir bff_service --reload --port 8002

# 5. Gateway (entrada pública local en puerto 8080)
API_KEY=biblioteca_dev_key_2026 JWT_SECRET=secreto-local-de-demostracion-longitud-suficiente \
  PRESTAMOS_URL=http://127.0.0.1:8000 BFF_URL=http://127.0.0.1:8002 \
  python -m uvicorn main:app --app-dir gateway_service --reload --port 8080
```

### Inicialización y Sembrado de las Bases de Datos

El arranque siembra solo bases vacías. Conserva 29 títulos, 66 copias disponibles y tres socios correlacionados por RUT; dos libres y uno sancionado. Los préstamos de demostración se crean por la Saga real. Una segunda ejecución no duplica ni modifica datos. También puede invocarse cada semilla directamente:

```bash
python catalogo_service/src/seed.py
API_KEY=biblioteca_dev_key_2026 python prestamos_service/src/seed.py
python sanciones_service/src/seed.py
```

`--reset` rechaza bases pobladas. `scripts/diagnosticar_consistencia.py` consulta copias estables de los tres almacenes en solo lectura, enumera discrepancias por ID y sale 2 si encuentra alguna.

### Funcionalidades y Patrones Implementados

- **BFF web (Integrante 2):** Agregación de tres fuentes, DTO orientado al cliente, deduplicación por libro, resultados parciales conservadores, escritura delegada/idempotente y métricas de sus propios límites y circuito.

- **Microservicio Sanciones y Resiliencia (Integrante 3):**
  - **Identificador de Negocio Compartido (`rut`):** Correlación de socios entre `prestamos_service` y `sanciones_service` mediante clave natural única (ej. `11.111.111-1`), desacoplando IDs autonuméricos locales.
  - **Módulo de Caos en Vivo:** Endpoints `/chaos/delay?seconds=X` y `/chaos/fault?status=500` para inducir lentitud y fallas controladas, además de `/chaos/reset` y `/chaos/status`.
  - **Timeout:** Tiempo límite estricto de 1.5s en llamadas remotas hacia Sanciones, conteniendo la lentitud artificial.
  - **Circuit Breaker efectivo de Saga:** Tres estados en `prestamos_service/src/circuit_breaker.py`. Tras tres fallos consecutivos abre y evita la red; recupera con una sonda. La latencia se mide en cada ejecución.
  - **Bulkhead efectivo de Saga:** Control por proceso en `prestamos_service/src/sanciones_client.py`, cinco llamadas por defecto, rechazo inmediato y cupos retenidos hasta terminar el transporte.
  - **Fallback conservador:** El cliente informa `EN_REVISION_MANUAL`, persistido como `EN_REVISION`; no autoriza entregar el ejemplar.
  - **Script de Demostración del Escenario 3:**
    ```bash
    python scripts/test_resilience_sanciones.py
    ```
    Ejecuta el perfil Compose del consumidor real: timeout, circuito abierto sin red, revisión, recuperación, Bulkhead y una lectura útil mientras Sanciones está lenta. El antiguo cliente aislado se conserva en `sanciones_service/src/client.py`; no sustituye esa evidencia.

- **Transacción Distribuida con Patrón Saga (Orquestador y Compensaciones):**
  - **Coordinación Centralizada:** Implementación del patrón Saga mediante orquestación (`PrestamoSagaOrchestrator`) para la operación de préstamo, coordinando Catálogo (gRPC), Sanciones (HTTP resiliente) y Préstamos (DB local).
  - **Compensación semántica inversa:** Progreso y acción pendiente persistidos. Anula el préstamo parcial si existe; Sanciones es lectura; después cancela por identidad en Catálogo. Solo marca COMPENSADA al confirmar todas las acciones; los fallos quedan pendientes y recuperables.
  - **Idempotencia Estricta:** Manejo de llaves únicas `idempotency_key` con tabla de persistencia `saga_transacciones`, retornando resultados precalculados ante reintentos sin duplicar operaciones ni reservas.
  - **Degradación Elegante:** Integración con la regla de negocio de Sanciones; si el verificador no responde o está en fallback, el préstamo se registra con estado `EN_REVISION`.
  - **Script de Demostración de Escenarios 1 y 2 (ABET 6):**
    ```bash
    python scripts/test_saga_escenarios.py
    ```
    Genera `resultado.json` con peticiones, duraciones, configuración y estados comprobados, además de logs y diagnóstico. El fallo controlado se habilita solo en el proyecto desechable y requiere JWT admin. También comprueba un fallo real SQLite.

- **Métodos Core gRPC (Catálogo):**
  - `ReservarEjemplar(ReservaRequest)`: Reserva atómica de ejemplar disponible (`DISPONIBLE` -> `PRESTADO`).
  - `LiberarEjemplar(LiberarRequest)`: Liberación de ejemplar prestado (`PRESTADO` -> `DISPONIBLE`).
  - `ConsultarDisponibilidad(ConsultaRequest)`: Consulta de conteo total y disponibles por libro.
  - `ListarCatalogo(CatalogoRequest)`: Lista el catálogo con filtros opcionales de género y disponibilidad.
  - `ObtenerLibro(LibroRequest)`: Obtiene la información detallada de un libro y sus ejemplares.
  - `ConsultarOperacion(OperacionRequest)`: Consulta asignación y estado por identidad persistente.
  - `CancelarReserva(CancelacionRequest)`: Cancela por identidad, incluso antes de recibir la reserva original.
- **HATEOAS (Préstamos):** Acciones POST de devolución para ACTIVO y cancelación para EN_REVISION, según estado.
- **Resiliencia Catálogo (ADR-004):** Circuit Breaker y política de reintentos con backoff exponencial sobre llamadas gRPC hacia Catálogo.
- **Manejo Estandarizado de Errores `{codigo, mensaje}`:** Respuestas estandarizadas en todas las APIs bajo el contrato OpenAPI.
- **Health Checks:** Endpoints `/health` en Préstamos, Sanciones, BFF y Gateway; disponibilidad gRPC para Catálogo.
- **Concurrencia SQLite:** Transacciones breves con `BEGIN IMMEDIATE`, sin llamadas de red dentro de la transacción. Se comprueban 20 reservas concurrentes sobre dos copias desechables.
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
- **Decisiones Arquitectónicas Propias:** Incluyen aislamiento mediante *Database-per-Service*, integración REST/gRPC, transacciones compatibles con SQLite y umbrales del disyuntor. Sus garantías se contrastan con las pruebas de cada bloque de corrección.
- **Verificación Práctica:** La corrección y fiabilidad del software fueron validadas mediante la ejecución exitosa de la suite de pruebas unitarias y el despliegue reproducible en contenedores Docker.
