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
      └── server.py               # Implementación de los métodos gRPC y Servicer
│   └── main.py                     # Entrypoint del servidor gRPC
│
└── prestamos_service/              # Servicio público Préstamos (REST API)
    ├── Dockerfile
    ├── requirements.txt
    ├── protos/                     # Cliente compilado a partir de catalogo.proto
    ├── src/
        ├── database.py             # Conexión engine y sesión de su propia BD
        ├── models.py               # Modelos Socio y Prestamo
        ├── grpc_client.py          # Lógica de llamada a Catálogo y manejo de fallas
        ├── schemas.py              # Esquemas Pydantic / DTOs
        ├── auth.py                 # Middleware o dependencia de auth (API Key / Bearer)
        └── routers/                # Endpoints versión v1
            ├── socios.py           # /v1/socios
            └── prestamos.py        # /v1/prestamos
    └── main.py                     # App FastAPI/Flask y lifespan (create_all)
```

## Ejecución Rápida con Docker Compose

El sistema está configurado para levantarse inmediatamente con un solo comando:

```bash
docker compose up --build
```

### Autenticación y Variable de Entorno `API_KEY`

La API de Préstamos requiere autenticación mediante el header `X-API-Key`:
- **Evaluación y desarrollo rápido (Zero-Config)**: Si no se define un archivo `.env`, Docker Compose inyecta automáticamente una clave por defecto para pruebas:
  ```
  biblioteca_dev_key_2026
  ```
- **Personalización opcional**: Si se desea definir una clave propia, basta con crear un archivo `.env` en la raíz (usando `.env.example` como plantilla):
  ```bash
  cp .env.example .env
  # Editar API_KEY en .env con la clave deseada
  ```

### URLs del Sistema (`docker compose up`)

| Recurso | URL | Autenticación |
|---|---|---|
| Documentación interactiva (Swagger UI) | http://localhost:8000/docs | Requerida (`X-API-Key`) |
| Especificación OpenAPI (JSON) | http://localhost:8000/openapi.json | Pública |
| Health check (REST) | http://localhost:8000/health | Pública |
| API — Socios | http://localhost:8000/v1/socios | Requerida (`X-API-Key`) |
| API — Préstamos | http://localhost:8000/v1/prestamos | Requerida (`X-API-Key`) |

### Cómo probar los endpoints protegidos

- **Desde Swagger UI**:
  1. Abrir http://localhost:8000/docs en el navegador.
  2. Hacer clic en el botón verde **Authorize** (arriba a la derecha).
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

## Servicio de Catálogo (gRPC)

El servicio de **Catálogo** administra el inventario de libros y la disponibilidad de ejemplares en el puerto gRPC `50051`.

### Compilación del Contrato gRPC (`contracts/catalogo.proto`)
Si se modifica el archivo `contracts/catalogo.proto`, los stubs de Python pueden recompilarse con el comando estándar de `protoc`:

```bash
python3 -m grpc_tools.protoc -Icontracts --python_out=catalogo_service/protos --grpc_python_out=catalogo_service/protos contracts/catalogo.proto
python3 -m grpc_tools.protoc -Icontracts --python_out=prestamos_service/protos --grpc_python_out=prestamos_service/protos contracts/catalogo.proto
```

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
- **gRPC Reflection (`grpc_reflection`):** Habilitado en el puerto `50051` para introspección dinámica con herramientas como `grpcurl` o Postman.
- **gRPC Health Check (`grpc.health.v1`):** Responde estado `SERVING` en el servicio `catalogo.Catalogo` para monitoreo de infraestructura.
- **Concurrencia Atómica:** Bloqueo de sesión SQLAlchemy (`with_for_update`) en reservas para evitar condiciones de carrera.
