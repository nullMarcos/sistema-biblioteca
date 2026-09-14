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
