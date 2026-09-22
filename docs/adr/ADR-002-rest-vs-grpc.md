# ADR-002 · REST hacia afuera, gRPC hacia adentro

**Estado:** aceptada

## Contexto

El sistema integra dos servicios con perfiles de consumo muy distintos. El servicio de Préstamos expone una API pública, consumida por actores externos: Personal de la organización a través de herramientas variadas, y un futuro portal web aún no definido. El servicio de Catálogo, en cambio, tiene un único cliente conocido de antemano: el propio servicio de Préstamos, que lo consulta en cada registro de préstamo para verificar disponibilidad, corresponde a una comunicación interna, de alto volumen, que nunca es vista por el exterior.

Esta diferencia de clientes y de volumen es la que obliga a decidir un protocolo de comunicación para cada frontera del sistema, en vez de usar uno solo para todo.

## Alternativas consideradas

Dada la arquitectura referencia del encargo, las siguientes alternativas son las mas logicas a considerar

- **Opción A — REST/JSON para ambas comunicaciones.** Un único protocolo en todo el sistema, simplificando el stack tecnológico y evitando que el equipo tenga que manejar dos paradigmas de comunicación distintos.
- **Opción B — gRPC para ambas comunicaciones**, incluyendo la cara pública. Aprovecha el rendimiento y el tipado fuerte de gRPC también para los clientes externos.
- **Opción C — REST hacia afuera (Préstamos), gRPC hacia adentro (Catálogo)**, tal como sugiere la arquitectura del encargo.

## Decisión

Se eligió la **Opción C**: la API de Préstamos expone REST/JSON hacia el exterior, y consume el servicio de Catálogo mediante gRPC internamente.

## Justificación

**Interoperabilidad del lado externo.** El cliente de la API pública no está definido de antemano, si bien los usuarios inmediatos son personas utilizandola directamente, eventualmente podria llegar a ser un portal web con un frontend bien definido. REST sobre HTTP/JSON es consumible desde cualquier lenguaje o herramienta sin dependencias especiales (un navegador, `curl`, Postman, cualquier librería HTTP), mientras que gRPC requiere generar stubs específicos por lenguaje a partir de un `.proto`, lo que implica una fricción injustificada para un cliente externo que ni siquiera está definido todavía. Del lado interno, en cambio, ambos extremos de la comunicación (Préstamos y Catálogo) son controlados por el mismo equipo, por lo que esa fricción de generación de stubs desaparece: el `.proto` se acuerda una sola vez entre ambos integrantes y ambos generan su código a partir de él.

**Tipado y contrato.** REST/JSON no impone tipado en el transporte, dos servicios pueden acordar informalmente la forma de un JSON y desviarse sin que nada lo detecte hasta el runtime. gRPC, en cambio, obliga a un contrato `.proto` explícito con tipos concretos por campo (`int32`, `string`, `bool`); un cambio incompatible en los mensajes rompe la compilación del cliente antes de llegar a producción. Para una comunicación interna de alto volumen y alta frecuencia como la consulta de disponibilidad, este tipado estricto reduce la superficie de errores silenciosos entre ambos servicios.

**Rendimiento.** La comunicación con Catálogo ocurre en cada registro de préstamo, y su latencia se suma directamente a la latencia percibida por el cliente externo de la API REST. gRPC serializa sus mensajes en Protocol Buffers, un formato binario compacto: a diferencia de JSON, que repite el nombre de cada clave como texto en cada mensaje, Protocol Buffers solo transmite el número de campo, lo que reduce considerablemente el tamaño de cada mensaje. gRPC además corre sobre HTTP/2, que permite multiplexar múltiples llamadas sobre una misma conexión TCP, evitando el costo de abrir una conexión nueva por cada request como puede ocurrir en implementaciones de REST/HTTP1.1. *(Nota: reemplazar este párrafo con las cifras del experimento de la Competencia 6 — tamaño de mensaje gRPC/protobuf vs. REST/JSON para el mismo payload de `ConsultaResponse`/`ReservaResponse`, medido en este proyecto.)*

**Depuración.** Este es el costo más visible de la elección, y juega en contra de gRPC: un mensaje JSON es legible directamente en cualquier herramienta (el navegador, `curl -v`, los logs), mientras que un mensaje Protocol Buffers es binario y requiere el `.proto` correspondiente para poder interpretarlo. Para la cara pública, donde distintos consumidores externos necesitan poder inspeccionar y depurar sus propias integraciones sin herramientas adicionales, esto habría sido un costo real. Para la comunicación interna, en cambio, el equipo ya cuenta con el `.proto` y con herramientas de desarrollo que decodifican el mensaje, por lo que el costo de depuración solo afecta en el entorno de desarrollo, y no se propaga a terceros.

**Caché.** REST/HTTP tiene semántica de caché estandarizada a nivel de protocolo (cabeceras `Cache-Control`, `ETag`, comportamiento cacheable de `GET`), aprovechable por proxies intermedios sin lógica adicional. gRPC no define caché a nivel de protocolo, cachear una respuesta de `ReservarEjemplar` es responsabilidad explícita de la aplicación (es lo que se implementa, opcionalmente, en O1 con Redis). Esto no invalida la elección: la cara pública, que sí se beneficia de caché HTTP estándar, es justamente la que se implementó en REST.

## Costo aceptado

Se acepta la complejidad de mantener dos paradigmas de comunicación distintos dentro del mismo sistema (dos formas de definir contratos, dos formas de generar código, dos formas de depurar), en vez de un único protocolo uniforme. Se acepta también la curva de aprendizaje adicional que implica gRPC para el equipo, frente a la opción más simple de usar REST en todos lados. Se considera que ambos costos son menores que el beneficio de rendimiento y tipado obtenido en la comunicación de alto volumen, y menores que el costo de imponer gRPC a un cliente externo aún no definido.

## Consecuencias

- Cualquier cambio en la comunicación interna Préstamos–Catálogo debe actualizar primero `contracts/catalogo.proto`, y regenerar el código cliente/servidor a partir de él, el `.proto` es la fuente de verdad de la cara interna, de la misma forma que `openapi.yaml` lo es para la cara externa.
- Si en el futuro apareciera un cliente externo con necesidad real de alto rendimiento (por ejemplo, un servicio de terceros con alto volumen de consultas), habría que reevaluar si conviene exponer una interfaz gRPC adicional para ese caso, sin reemplazar la API REST existente para los demás consumidores.
- El manejo de fallas de Catálogo (ADR-004) depende directamente de esta elección: los errores de gRPC (`grpc.RpcError`) se traducen explícitamente a códigos HTTP (`503`) en la frontera REST, ya que ambos protocolos manejan errores de forma distinta y no pueden propagarse tal cual de uno a otro.
