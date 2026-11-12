# ADR-005 · Coordinación de Transacciones Distribuidas mediante Patrón Saga e Idempotencia

**Estado:** Aceptada

## Contexto

El flujo de negocio de préstamo de un libro involucra coordinar tres servicios independientes bajo el patrón *Database-per-Service*: Catálogo (`catalogo_service` expuesto vía gRPC), Sanciones (`sanciones_service` expuesto vía HTTP/REST) y Préstamos (`prestamos_service` con su base de datos relacional local).

En este entorno distribuido, la operación de préstamo requiere realizar cambios en el inventario de Catálogo (reserva de ejemplar), verificar la idoneidad del socio en Sanciones, y registrar el préstamo en la base de datos local de Préstamos. Ante caídas de red, indisponibilidad de servicios dependientes o violaciones de reglas de negocio en pasos posteriores (por ejemplo, detección de sanciones activas o error de escritura en la base de datos local), el sistema debe garantizar que el inventario de Catálogo no quede en un estado inconsistente (reserva huérfana).

Asimismo, debido a posibles reintentos de red por parte de los clientes o del API Gateway ante retardos transitorios, se requiere garantizar idempotencia estricta en la operación para evitar préstamos duplicados o dobles reservas de inventario.

## Alternativas consideradas

- **Opción A — Two-Phase Commit (2PC) / Transacciones Distribuidas XA.**
  Coordina una transacción distribuida atómica mediante un coordinador global en dos fases (Prepare y Commit).
  - *Ventaja:* Garantiza consistencia inmediata y aislamiento estricto (propiedades ACID completas).
  - *Desventaja crítica:* Requiere bloqueos pesimistas distribuidos durante la fase de preparación, reteniendo recursos en todos los participantes y degradando severamente la disponibilidad del sistema ante latencia o particiones de red (conflicto con el teorema CAP). Además, los servicios REST sobre HTTP y las bases de datos ligeras (como SQLite) no implementan el protocolo XA ni el estándar de bloqueo distribuido de dos fases.

- **Opción B — Patrón Saga Coreografiada (Event-Driven).**
  Cada microservicio ejecuta su transacción local y publica eventos de dominio en un bus o cola de mensajes; los demás servicios escuchan dichos eventos y ejecutan transacciones subsecuentes o compensaciones.
  - *Ventaja:* Desacoplamiento temporal completo entre servicios y alta escalabilidad horizontal sin coordinador central.
  - *Desventaja crítica:* Requiere introducir infraestructura adicional de mensajería (broker de eventos como Kafka o RabbitMQ), introduce riesgo de dependencias circulares de eventos, dificulta el rastreo del estado global de la transacción y hace que la auditoría y el orden determinista de compensación sean complejos de verificar y probar.

- **Opción C — Patrón Saga Orquestada con Compensaciones Semánticas en Orden Inverso (LIFO) e Idempotencia Estricta.**
  Un coordinador centralizado (`PrestamoSagaOrchestrator` en `prestamos_service`) gestiona el flujo de pasos hacia adelante de forma secuencial, registra cada paso ejecutado exitosamente en una pila de ejecución y, ante cualquier excepción o fallo de negocio, ejecuta compensaciones semánticas en orden estrictamente inverso ($C_3 \to C_2 \to C_1$). Además, persiste el estado de la transacción y su resultado asociado a una clave única `idempotency_key`.
  - *Ventaja:* Centralización del control y de la máquina de estados, auditoría y observabilidad directa de cada etapa del ciclo de vida del préstamo, ejecución determinista de compensaciones inversas, ausencia de infraestructura de mensajería adicional y prevención efectiva de dobles reservas mediante deduplicación.
  - *Desventaja:* El orquestador concentra la lógica de coordinación del flujo de negocio y se acepta consistencia eventual (BASE) en lugar de consistencia inmediata (ACID).

## Decisión

Se adopta la **Opción C**: implementar el **Patrón Saga mediante Orquestación** en `prestamos_service`, con compensación semántica en orden inverso (LIFO) e idempotencia estricta basada en persistencia relacional.

El flujo de pasos coordinado por `PrestamoSagaOrchestrator` es:
1. **Paso 1 (Catálogo gRPC):** Invocación de `ReservarEjemplar`. Compensación semántica asociada: `LiberarEjemplar`.
2. **Paso 2 (Sanciones HTTP):** Invocación de `verificar_sanciones` mediante cliente resiliente. Compensación semántica: Ninguna (operación de solo lectura / no-op). Si el socio registra sanciones vigentes, la Saga se aborta y se gatilla la compensación del Paso 1. Si el servicio de Sanciones se encuentra degradado o inaccesible (circuito abierto o timeout), se aplica degradación elegante pasando el préstamo a estado `EN_REVISION`.
3. **Paso 3 (Préstamos Local):** Inserción del registro del préstamo en la base de datos local de Préstamos. Compensación: Reversión de la transacción local.

Si el Paso 3 falla (o se induce fallo de prueba mediante la bandera `simular_fallo_paso3`), la compensación LIFO invoca automáticamente `LiberarEjemplar` en Catálogo, restaurando el stock a `DISPONIBLE` y dejando cero registros huérfanos.

Para asegurar idempotencia, cada solicitud incluye un identificador único `idempotency_key`. La tabla relacional `saga_transacciones` almacena el estado (`PENDIENTE`, `COMPLETADA`, `COMPENSADA`, `FALLIDA`) y la respuesta serializada. Ante solicitudes repetidas con la misma clave, el orquestador retorna inmediatamente la respuesta precalculada sin repetir reservas ni alterar el inventario.

## Justificación

1. **Alineación con el Teorema CAP y Disponibilidad:** En una arquitectura de microservicios con llamadas de red, priorizar disponibilidad y tolerancia a particiones (AP) requiere abandonar bloqueos distribuidos síncronos (2PC). Saga permite que cada servicio opere con transacciones ACID locales acotadas en su base de datos.
2. **Factibilidad Tecnológica:** Ni el servicio externo de Sanciones (HTTP/REST) ni Catálogo (gRPC/SQLite) soportan transacciones XA. Forzar 2PC sería técnicamente inviable sin reemplazar la pila tecnológica completa.
3. **Control y Auditoría:** La orquestación permite conocer en todo momento el estado de la transacción distribuida (`saga_id`), facilitando la depuración, la generación de métricas y la trazabilidad requerida para la verificación experimental (ABET 6).
4. **Idempotencia como Requisito de Integridad:** En redes no confiables donde pueden ocurrir retransmisiones automáticas o reintentos por timeout, la llave de idempotencia garantiza que la reserva y creación del préstamo se ejecuten exactamente una vez (*effectively once*).

## Costo aceptado

- **Consistencia Eventual:** Se acepta que durante la ventana temporal entre la reserva en Catálogo y la compensación (en caso de fallo en el Paso 3), el ejemplar figura temporalmente como `PRESTADO`. La consistencia del sistema es eventual (BASE), no inmediata.
- **Complejidad de Mantenimiento de Compensaciones:** Requiere implementar y probar rigurosamente las operaciones de compensación para asegurar que sean a su vez idempotentes y que no introduzcan fallas secundarias.

## Consecuencias

- El servicio de Préstamos garantiza que ante fallos en la verificación de sanciones o en la base de datos local, el catálogo de libros mantenga integridad absoluta de inventario, liberando reservas huérfanas.
- El sistema es inmune a peticiones duplicadas provenientes de clientes o reintentos del API Gateway mediante el uso de `idempotency_key`.
- Se dispone de trazabilidad granular para el monitoreo de transacciones distribuidas en producción y auditorías de inconsistencias.
