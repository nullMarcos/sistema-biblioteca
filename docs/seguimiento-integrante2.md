# Seguimiento del integrante 2

## Base y forma de trabajo

- C00: Matías confirmó `fix/entrega1-codigo`, árbol limpio y HEAD `47e4f4d023455485ba58b25c8efd7e009cb791d1`.
- Autor confirmado por Matías: Matías Figueroa. Sol no ejecuta comandos Git.
- La copia local antigua de ADR-002 quedó respaldada por Matías fuera del repositorio al actualizar la base.
- Se conserva el plan original. Solo se agrupan los momentos de commit: G01=C01–C03; G02=C04–C09; G03=C10–C13; G04=C14–C21; G05=C22–C26. Cinco commits de trabajo y hasta tres integraciones de PR: máximo ocho, sin reescribir historial.
- Un PR por fase; fase B después de PR-A integrado, fase C después de PR-B integrado.
- Commits nuevos confirmados para este plan: 0. El avance rápido de la base no creó un commit.

## Matriz inicial C00 (inspección estática, sin certificar ejecución)

| Hallazgo | Estado inicial | Evidencia / comprobación que falta |
|---|---|---|
| H1 | persiste | `with_for_update()` con SQLite; concurrencia de C04 pendiente |
| H2 | persiste | Reserva/liberación sin identidad remota; pérdidas de respuesta de C05 pendientes |
| H3 | parcial | Existe Saga; confirmaciones separadas y devolución no recuperable; C07–C09 pendientes |
| H4 | persiste | gRPC: 2 s y 2 reintentos; medición del presupuesto total en C12 pendiente |
| H5 | parcial | Compose no publica Catálogo; bindings efectivos por comprobar en C03 |
| H6 | persiste | Sin protección transaccional de duplicados/cupo; C06 pendiente |
| H7 | persiste | Libro inexistente devuelve disponibilidad 0/0; C04 pendiente |
| H8 | persiste | Dependencias abiertas e imágenes sin digest; C02 pendiente al iniciar |
| H9 | persiste | Stubs copiados y sin generación en Docker; C02 pendiente al iniciar |
| H10 | persiste | Tests ignorados y script Gateway que puede dar falso verde; C01/C13 pendientes al iniciar |
| H11 | parcial | Existen 503/504 distintos; comprobación integral de contrato en C11 pendiente |
| H12 | solo documental | Referencias del informe anterior; no crear una corrección artificial de código |
| H13 | persiste | Inventario semilla PRESTADO sin correspondencia completa; C10 pendiente |
| H14 | parcial | Fortalezas a conservar: errores granulares, HATEOAS y bases separadas; regresiones pendientes |

Riesgos adicionales de §4.1, observados estáticamente: replay sin comparar payload; reinserción de claves terminales; reserva antes de persistir progreso; commits locales separados; anulación fallida ocultada; liberación sin propietario; cliente de Sanciones distinto en pruebas; HALF_OPEN sin sonda única; respuesta incompleta interpretada como aprobada; exit code/aislamiento insuficiente del script Gateway; timeout Gateway 5 s frente a tramo gRPC nominal 6,3 s; OpenAPI/README desalineados. Se reproducirán en sus fichas; no están certificados como corregidos.

## G01 — C01–C03 (verificado, pendiente de commit manual)

- C01: fixtures con BD desechables y suites separadas; validación, socio inexistente, serialización REST y lectura/reserva/liberación gRPC secuenciales. No se afirma concurrencia ni idempotencia distribuida.
- C02: Python de dominio 3.14.10 y Gateway 3.12.16, imágenes multi-arquitectura fijadas por digest real; SQLAlchemy permanece en 2.0. Se conservan grpcio/grpcio-tools 1.84.0 y protobuf 7.35.1, que corresponden a los stubs base.
- Un generador desde `contracts/catalogo.proto` sirve a Docker y desarrollo. La primera comprobación detectó deriva real en los dos `_pb2_grpc.py`: un comentario antiguo `(Integrante 3)` ya no figuraba en el contrato. Se regeneraron, sin cambiar mensajes/RPC ni relajar el check.
- Constraints fija directas y transitivas; `greenlet` está fijado para su instalación condicional Linux. `grpcio-tools` permanece en la imagen por simplicidad del build, aceptando ese tamaño adicional.
- Runner de migraciones preparado y probado con BD existente, replay, checksum y rollback. La revisión reprodujo un fallo del runner nuevo: un `COMMIT` dentro del SQL podía dejar cambios parciales. Se bloquean controles de transacción/savepoints mediante el authorizer de SQLite; la regresión falla antes del arreglo y pasa después. No hay cambios de esquema de negocio ni migraciones aplicadas a datos del usuario en este bloque.
- C03: contextos allowlist por Dockerfile, nombres/red por proyecto, puerto Gateway configurable y healthchecks. Préstamos no depende del arranque de Sanciones; su liveness y su diagnóstico `/health` se mantienen separados.
- El ensayo de Compose selecciona explícitamente el YAML y un env-file vacío; fija Sanciones por DNS interno. Así evita heredar overrides, secretos o destinos externos de la configuración local. La limpieza intenta ambos proyectos y falla si no puede completarse.

### Decisiones y costes aceptados

| Problema y alternativas | Decisión y razón | Coste aceptado / relación con el plan |
|---|---|---|
| Los servicios comparten el nombre de paquete `src`; una sola colección puede mezclar módulos | Suites en subprocesos separados y fixtures SQLite temporales | Varias invocaciones de pytest; aislamiento verificable para H10 y ABET 6 |
| El contrato queda fuera del antiguo contexto de servicio; enviar todo el repositorio ampliaría innecesariamente el contexto | Contexto raíz con allowlist por Dockerfile y COPY explícitos | Mantener una allowlist por servicio; C02/H8/H9, sin incluir BD ni secretos |
| Stubs manuales pueden divergir; eliminarlos obligaría a generarlos para cualquier import local | Mantenerlos versionados para desarrollo, generarlos siempre en Docker y exigir `--check` | Regenerar los dos consumidores al cambiar el contrato; C02/H9 |
| `create_all()` no amplía tablas existentes; Alembic agregaría una dependencia y configuración | Runner SQL incremental pequeño, checksum y transacción bajo control del runner | Migraciones escritas explícitamente, sin autogeneración; C02 y preservación de datos para C05 en adelante |
| `/health` de Préstamos incluye dependencias; usarlo como barrera de arranque puede impedir lecturas útiles | Healthcheck de proceso mediante `/openapi.json`, dependencia de arranque solo en Catálogo | No certifica todas las dependencias; el `--wait` del proyecto completo sí espera a Sanciones. C03 y requisito 3.1 |

### Alcance de scripts previos inspeccionados

| Script | Qué cubre realmente | Pendiente |
|---|---|---|
| `test_gateway.sh` | Login, rutas y rate limit; puerto parametrizado en C03 | Acumular fallos/exit code y sustituir HTTP-a-gRPC por aislamiento real en C13 |
| `test_saga_escenarios.py` | Happy path, replay secuencial, fallo sintético paso 3 y rechazo sancionado | No prueba concurrencia ni reinicio ni fallo real de commit; usa archivos `/tmp` fijos; C07–C13 |
| `test_resilience_sanciones.py` | Cliente async aislado de Sanciones | No es el consumidor que usa la Saga; resetea su BD y anuncia cifras fijas; C12/C24 |

No se ejecutaron los scripts antiguos como evidencia de G01; sus datos y controles se aislarán en las fichas correspondientes.

### Comprobaciones ejecutadas

- `.venv/bin/python -m pip install -r requirements-dev.txt`: instalación correcta en Python local 3.13.7.
- `.venv/bin/python scripts/generar_protos.py`: generación correcta tras diagnosticar la deriva.
- `.venv/bin/python scripts/test_unitarios.py`: resultado final 4 pruebas comunes + 3 gRPC + 5 REST aprobadas (12 en total); un aviso de deprecación de Starlette sobre TestClient/httpx, sin suprimirlo.
- `.venv/bin/python -m pip check`: sin incompatibilidades declaradas.
- `docker compose config --quiet`: configuración válida.
- `docker compose -p bibg01build build --no-cache`: cuatro builds aprobados en Linux/arm64; `pip check` correcto en cada imagen y stubs generados en Catálogo y Préstamos.
- `.venv/bin/python scripts/verificar_compose.py`: dos ejecuciones aprobadas; la última con lectura gRPC real, configuración aislada y limpieza de imágenes. Proyectos finales `bibg011b58de39c8a` y `bibg011b58de39c8b`: ocho contenedores saludables, dos redes y seis volúmenes sin compartir; bindings solo en Gateway, TCP del Gateway accesible, DNS/HTTP/gRPC internos correctos, escritura no visible entre proyectos y Préstamos recreado sin Sanciones conservando lecturas/datos. Limpieza terminada con exit code 0.
- `bash -n scripts/test_gateway.sh`: sintaxis válida; no es una ejecución funcional del script anterior.
- Revisión de archivos sin Git: 15 archivos existentes modificados y 18 nuevos; sintaxis Python válida, sin espacios finales añadidos ni cambios de BD locales. El SHA-256 del plan original sigue siendo `3813b5a2cf6d8d106349fecf0df1de48d369c06dba1132c7560e1c355c53df25`.
- Inspección posterior: no quedan contenedores, redes ni volúmenes de los cuatro proyectos ensayados. Los tres contenedores preexistentes conservan sus IDs y estados. Se eliminaron también las etiquetas de imagen de la primera ejecución de prueba.

### Cobertura cerrada de este bloque

- H5: acceso por bindings comprobado en proyectos reales; Catálogo sigue interno.
- H8/H9: versiones y bases fijadas, build limpio, generación única y detección de deriva comprobadas.
- H10: exclusión de tests retirada y nuevas suites con exit code significativo; la reparación funcional del script Gateway corresponde a C13.
- C03: nombres por proyecto, DNS, tres almacenes independientes y puerto Gateway configurable; aporta evidencia del requisito 3.1.
- No se certifican aquí reserva concurrente, idempotencia distribuida, recuperación Saga, semillas ni resiliencia del consumidor efectivo. Sus regresiones y correcciones siguen en C04–C13.

Siguiente paso: Matías revisa y ejecuta el commit agrupado G01; confirmar su SHA y estado antes de C04. Continuar en `fix/entrega1-codigo`; todavía no abrir PR.
