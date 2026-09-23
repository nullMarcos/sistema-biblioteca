# ADR-003 · Contrato, versionado y evolución

Estado: aceptada

## Contexto

- **REST:** `contracts/openapi.yaml` describe la API pública de Préstamos: rutas de negocio `/v1/socios` y `/v1/prestamos`, además de `/health`. Declara OpenAPI 3.0.4 y `info.version: "1.0.0"`; la versión de la especificación, del documento y el prefijo API `/v1` son conceptos distintos.
- **gRPC:** `contracts/catalogo.proto` define `package catalogo`, el servicio `Catalogo` y sus cinco RPC. Es la interfaz lógica interna de Catálogo y sus consumidores, incluido Préstamos.

REST tiene consumidores externos; gRPC conecta servicios internos. Ambos son contratos de interfaz para consumidores que pueden desplegarse en momentos distintos. Un cambio puede romperlos aunque el servicio compile localmente.

## Alternativas consideradas

| Alternativa | Ventaja | Costo | Riesgo |
|---|---|---|---|
| **A.** Modificar siempre la misma versión | No mantiene rutas/contratos paralelos. | Requiere coordinar todos los consumidores. | Una ruptura puede interrumpir clientes desactualizados. |
| **B.** Crear versión por cualquier cambio | Cada versión publicada permanece estable. | Duplica contratos, pruebas y documentación incluso para adiciones compatibles. | Fragmentación y divergencia innecesarias entre versiones. |
| **C.** Compatibles en versión actual; nueva versión ante rupturas | Evolución gradual; migración solo cuando es necesaria. | Revisión de compatibilidad y convivencia temporal. | Clasificar mal una ruptura puede dañar clientes; se mitiga con PR y pruebas. |

## Decisión

Se adopta **C** y el proceso *contract-first*: proponer y revisar primero el contrato, conservar cambios compatibles en la versión actual y crear una versión mayor para cambios incompatibles.

### REST

| Cambio | Clasificación general | Acción |
|---|---|---|
| Endpoint/operación independiente, parámetro query opcional o campo opcional de respuesta | Compatible | Mantener en `/v1`, si no altera el comportamiento existente. Un consumidor con validación estricta podría rechazar campos nuevos. |
| Eliminar/renombrar ruta o campo; cambiar tipo o semántica; volver obligatorio algo opcional; cambiar radicalmente comportamiento o respuestas | Potencialmente incompatible | Introducir `/v2` en las rutas afectadas y actualizar OpenAPI. |

`/v1` y `/v2` conviven durante la migración. Antes de retirar v1 se marca la operación como deprecada (`deprecated: true`) y se comunica motivo, alternativa, pasos y fecha prevista de retiro. La ventana se acuerda según impacto y consumidores; no se fija aquí un plazo en meses y v1 no se elimina al publicar v2.

### Protobuf y gRPC

Agregar un campo con un número nunca utilizado es normalmente compatible con el wire format. En el `LibroDTO` actual, 8 está libre:

```proto
string editorial = 8;
```

Clientes antiguos ignoran campos desconocidos; los runtimes Protobuf conservan esos campos al parsear y volver a serializar el mensaje binario. Clientes nuevos reciben el default proto3 (`""` para `string`) al leer mensajes antiguos. Si importa distinguir ausencia de cadena vacía, usar presencia explícita (`optional`). La conversión a JSON o reconstrucción campo a campo puede descartar campos desconocidos.

Renumerar, reutilizar un número o cambiar su significado es incompatible. Cambiar tipo/cardinalidad puede romper wire o código consumidor; incluso cambios wire-compatible pueden perder datos, por lo que requieren análisis y despliegue coordinado. Al eliminar un campo, reservar número y nombre:

```proto
reserved 8;
reserved "editorial";
```

La reserva impide reutilización accidental y ambigüedad al interpretar datos antiguos; reservar el nombre también protege representaciones JSON/textuales. La eliminación puede ser segura para parseo binario, pero romper consumidores que dependan del dato: comprobar dependencias y, si rompe su semántica, introducir versión nueva. Si el cambio es compatible, se conserva `package catalogo`; una ruptura puede versionarse como `package catalogo.v2;` y servicio equivalente. v1 y v2 gRPC conviven hasta migrar los consumidores. Todo cambio `.proto` requiere regenerar los stubs afectados; esta ADR no modifica ni implementa el esquema.

### Proceso de cambio

1. Proponer cambio, motivo, consumidores afectados y comportamiento esperado.
2. Modificar primero `contracts/openapi.yaml` o `contracts/catalogo.proto`.
3. Revisar compatibilidad binaria, semántica, valores por defecto y migración.
4. Aprobar el cambio mediante PR.
5. Actualizar documentación y guía de migración.
6. Si cambia `.proto`, regenerar y revisar stubs.
7. Actualizar el productor; después, consumidores.
8. Para rupturas, desplegar v2 y mantener v1 durante la transición.
9. Ejecutar validaciones del contrato y pruebas de productor/consumidores.
10. Liberar y comunicar versión, deprecación y fechas acordadas.

PR e historial Git dan trazabilidad. Cada liberación se comunica mediante release/tag y CHANGELOG o sección de cambios. OpenAPI muestra la deprecación; las notas indican ruta de migración y retiro. Para gRPC se identifican el cambio `.proto` y los stubs. Se usan los canales de contacto/distribución ya disponibles, sin presuponer una plataforma adicional.

## Justificación

C evita migraciones innecesarias por cambios compatibles y reduce el riesgo de cortar clientes con ciclos de despliegue independientes. La compatibilidad del wire format no garantiza compatibilidad de negocio; por eso los cambios inciertos se tratan como potencialmente incompatibles y se validan mediante PR y pruebas.

**Fuentes oficiales consultadas:** [Protocol Buffers proto3](https://protobuf.dev/programming-guides/proto3/) (field numbers, unknown fields, defaults, `reserved`); [OpenAPI 3.0.4](https://spec.openapis.org/oas/v3.0.4.html) (contrato HTTP, versiones y `deprecated`); [gRPC Core concepts](https://grpc.io/docs/what-is-grpc/core-concepts/) (servicios Protobuf y stubs). Consultadas el 22-09-2026. Las políticas de versionado de esta ADR son decisiones del proyecto.

## Costo aceptado

La convivencia temporal implica **más código, pruebas, documentación y mantenimiento**, además de coordinar productores, consumidores y regeneración de stubs. Se acepta porque reduce el riesgo de romper consumidores y permite migraciones planificables; no se fija un número de meses sin acuerdo del equipo.

## Consecuencias

**Positivas:** evolución predecible, interoperabilidad para cambios compatibles, migraciones planificables y trazabilidad en contratos, PR, Git y versiones.

**Negativas:** disciplina y revisión de compatibilidad adicionales; costo de versiones coexistentes; pruebas, documentación y mantenimiento hasta completar migraciones.

### Defensa

- **¿Agregar `editorial = 8`?** Compatible si 8 nunca se usó ni reservó; clientes antiguos lo ignoran y los nuevos ven `""` si falta. Regenerar stubs.
- **¿Eliminar `genero = 4`?** Sus consumidores pueden perder el dato/default; si rompe semántica, migrar con versión nueva. Reservar `4` y `"genero"`.
- **¿Por qué no reutilizar 4?** Bytes antiguos podrían interpretarse como otro concepto y causar ambigüedad o corrupción semántica.
- **¿Cuándo crear `/v2`?** Ante ruptura de `/v1` —por ejemplo, eliminar/renombrar/cambiar tipo o semántica, volver obligatorio un campo o alterar radicalmente una operación—, no por adiciones compatibles.
- **¿Cómo se avisa?** Contrato actualizado, PR/historial, release/tag, notas de cambios y marca deprecada en OpenAPI; para gRPC, cambio `.proto` y stubs actualizados.
- **¿Costo de v1 y v2?** Más código, pruebas, documentación y mantenimiento temporal; se acepta para reducir rupturas y habilitar migraciones graduales.
