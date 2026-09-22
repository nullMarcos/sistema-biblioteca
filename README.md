# Sistema de Gestión de Biblioteca

## Integrantes: 

- Matías Figueroa 
- Daniel Támaro
- Gabriel Castillo 
- Marcos Martínez 

## Descripción de la estructura del proyecto

```bash
sistema-biblioteca/
├── docker-compose.yml              # Levanta ambos servicios y sus redes/volúmenes
├── README.md                       # Instrucciones de ejecución y declaración de IA
│
├── contracts/                      # Contratos explícitos compartidos
│   ├── openapi.yaml                # Especificación OpenAPI de la API REST
│   └── catalogo.proto              # Definición de servicios y mensajes gRPC
│
├── docs/                           # Documentación de ingeniería y experimentos
│   ├── adr/                        # Architecture Decision Records
│   │   ├── ADR-001-estilo-descomposicion.md
│   │   ├── ADR-002-rest-vs-grpc.md
│   │   ├── ADR-003-contratos-versionado.md
│   │   └── ADR-004-resiliencia-modos-falla.md
│   └── experimentos/               # Scripts de medición, datos y gráficos (ABET 6)
│       ├── benchmark.py
│       └── results.png
│
├── catalogo_service/               # Servicio interno Catálogo (gRPC)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── protos/                     # .proto compilado (_pb2.py y _pb2_grpc.py)
│   ├── src/
│   │   ├── database.py             # Conexión engine y sesión SQLite/SQLAlchemy
│   │   ├── models.py               # Modelos Libro y Ejemplar
│   │   ├── seed.py                 # Datos de prueba iniciales de libros/copias
│   │   └── server.py               # Implementación de los métodos gRPC
│   └── main.py                     # Entrypoint del servidor gRPC
│
└── prestamos_service/              # Servicio público Préstamos (REST API)
    ├── Dockerfile
    ├── requirements.txt
    ├── protos/                     # Cliente compilado a partir de catalogo.proto
    ├── src/
    │   ├── database.py             # Conexión engine y sesión de su propia BD
    │   ├── models.py               # Modelos Socio y Prestamo
    │   ├── grpc_client.py          # Lógica de llamada a Catálogo y manejo de fallas
    │   ├── schemas.py              # Esquemas Pydantic / DTOs
    │   ├── auth.py                 # Middleware o dependencia de auth (API Key / Bearer)
    │   └── routers/                # Endpoints versión v1
    │       ├── socios.py           # /v1/socios
    │       └── prestamos.py        # /v1/prestamos
    └── main.py                     # App FastAPI/Flask y lifespan (create_all)
```
## Configuración antes de levantar el sistema

Este proyecto requiere una API Key para autenticar las peticiones a la API de Préstamos. Antes de correr `docker compose up`:

1. Crear un archivo `.env` en la raíz del repositorio, usando `.env.example` como guía, y completar `API_KEY` con la clave real.
2. Editar `.env` y definir un valor real para `API_KEY` (no dejar el placeholder — el sistema falla al arrancar si la variable no está definida, a propósito).

> **No subir el `.env` al repositorio.** Ya está excluido en `.gitignore`; solo `.env.example` debe versionarse.

### URLs una vez levantado (`docker compose up`)

| Recurso | URL |
|---|---|
| Documentación interactiva (Swagger UI) | http://localhost:8000/docs |
| Especificación OpenAPI (JSON) | http://localhost:8000/openapi.json |
| Health check | http://localhost:8000/health |
| API — Socios | http://localhost:8000/v1/socios |
| API — Préstamos | http://localhost:8000/v1/prestamos |

Los endpoints de `/v1/*` no se pueden visitar directo desde el navegador — requieren el header `X-API-Key`, que un navegador no manda por sí solo. Para probarlos:

- **Desde Swagger UI**: En `/docs`, hacer clic en **Authorize**, pegar el valor de `API_KEY` del `.env`, y usar "Try it out" en cada endpoint.

- **Desde la terminal**:

- **Linux / Mac**:
```bash
  curl -H "X-API-Key: [LA API_KEY VA ACÁ]" http://localhost:8000/v1/socios
```
- **Windows (PowerShell)**:
```powershell
  curl.exe -H "X-API-Key: [LA API_KEY VA ACÁ]" http://localhost:8000/v1/socios
```