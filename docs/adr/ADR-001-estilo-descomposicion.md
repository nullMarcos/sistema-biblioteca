# ADR-001 · Estilo de integración y descomposición

**Estado:** aceptada

## Contexto

El sistema de gestión de biblioteca requiere separar la lógica de negocio para coordinar el trabajo del equipo. Existe un servicio de Préstamos (gestión de socios y préstamos) y un servicio de Catálogo (inventario de libros y disponibilidad de ejemplares). Se requiere definir la frontera entre ellos, el estilo de integración para la comunicación, y la distribución de la base de datos para no tener un repositorio compartido.

## Alternativas consideradas

- **Opción A — Monolito.** Menos complejidad de red, pero acopla el trabajo de ambos equipos y la base de datos sería la misma, infringiendo los requisitos obligatorios del encargo.
- **Opción B — Integración asíncrona (Eventos/Cola).** Desacopla la disponibilidad temporal, pero aumenta fuertemente la complejidad de infraestructura (requiere RabbitMQ, Kafka o similares) y dificulta enormemente el manejo sincrónico de reservas.
- **Opción C — Servicios separados (REST API + gRPC).** Cada servicio tiene su bounded context y base de datos propia. Préstamos expone hacia afuera vía REST, y Catálogo expone internamente vía gRPC para alta performance en consultas repetitivas.

## Decisión

Se eligió la **Opción C**: una arquitectura de servicios separados con integración síncrona mediante gRPC para comunicación interna y REST hacia afuera.

## Justificación

Esta estructura divide las responsabilidades claramente bajo el principio de *bounded context*. Catálogo es el sistema de registro (fuente de verdad) del inventario, y Préstamos lo consulta. Utilizar gRPC (Protocol Buffers) optimiza significativamente el ancho de banda y la latencia para la comunicación interna que se espera tenga un volumen muy alto de lecturas de disponibilidad. Por su parte, el diseño con bases de datos independientes por servicio asegura el aislamiento exigido (T5).

## Costo aceptado

Se acepta un alto acoplamiento temporal: el servicio de Préstamos fallará o verá su capacidad limitada si el servicio de Catálogo se encuentra caído o inaccesible, requiriendo un manejo explícito de fallas (como circuit breakers o retries controlados) del lado de Préstamos.

## Consecuencias

- Cada equipo puede evolucionar y desplegar su respectivo servicio independientemente, siempre que respeten el contrato.
- Cualquier cambio en la comunicación interna (el archivo `.proto`) requerirá coordinación activa entre ambos equipos para actualizar y regenerar los stubs en Python.
