# ADR-004 · Resiliencia y Modos de Falla en la Integración gRPC

**Estado:** Aceptada

**Contexto:**  
El servicio de Préstamos (API REST) depende de forma síncrona del servicio interno de Catálogo (gRPC) para reservar y liberar ejemplares de libros durante las transacciones de préstamo. Si el servicio de Catálogo experimenta latencia excesiva, fallas transitorias de red o caídas totales, las peticiones HTTP entrantes corren el riesgo de bloquear hilos de ejecución, agotar el pool de conexiones y provocar fallos en cascada en toda la plataforma. Se requiere definir una política explícita de tolerancia a fallos que proteja los recursos locales, tolere intermitencias menores y devuelva errores claros y estandarizados según el contrato OpenAPI.

**Alternativas consideradas:**  
* **Opción A — Fail-Fast Inmediato sin Reintentos ni Disyuntor:** Ofrece una implementación trivial (captura de excepción y retorno inmediato de HTTP 503), pero cuesta una tasa alta de rechazos ante parpadeos de red transitorios y no previene la saturación del servidor por llamadas repetidas durante caídas prolongadas.
* **Opción B — Reintentos Ilimitados con Timeout Prolongado:** Ofrece una alta probabilidad de completar la transacción ante contingencias, pero cuesta retener recursos de CPU/red y bloquear a los clientes HTTP indefinidamente, arriesgando el colapso del servicio de Préstamos por agotamiento de sockets.
* **Opción C — Reintentos Acotados con Backoff Exponencial, Circuit Breaker (Disyuntor) y Health Check Dependiente:** Ofrece tolerancia automática a fallas transitorias breves, corte inmediato de tráfico (*fail-fast*) cuando Catálogo colapsa de forma sostenida y observabilidad de dependencias, pero cuesta mayor complejidad de código, gestión de concurrencia en memoria y una breve latencia añadida en peticiones que experimentan reintentos.

**Decisión:**  
Se adopta la **Opción C**, implementando reintentos acotados (máximo 2 reintentos con backoff exponencial para fallos transitorios `UNAVAILABLE` y `DEADLINE_EXCEEDED`), un patrón Circuit Breaker en memoria (`CLOSED`, `OPEN`, `HALF_OPEN`) con umbral de 3 fallos consecutivos y ventana de recuperación de 10 segundos, y un endpoint `/health` dependiente.

**Justificación:**  
La Opción C proporciona el mejor balance entre disponibilidad, protección de recursos y experiencia de usuario. En pruebas de estrés y escenarios de falla, el backoff exponencial (0.1s, 0.2s) permite recuperar peticiones ante micro-cortes de red en menos de 300 ms sin trasladar el error al cliente; mientras que, ante la caída total del servicio de Catálogo, el Circuit Breaker corta el flujo a partir del tercer fallo consecutivo y responde de forma instantánea (<1 ms) con HTTP 503 (`CIRCUITO_ABIERTO`), liberando inmediatamente los workers de FastAPI en lugar de retenerlos durante el timeout de 2 segundos de gRPC. Adicionalmente, la diferenciación estricta de códigos de estado de gRPC evita reintentos inútiles ante errores deterministas (`NOT_FOUND`, `INVALID_ARGUMENT` o falta de stock).

**Costo aceptado:**  
Se asume la complejidad añadida de gestionar sincronización de concurrencia (`threading.Lock`) para el estado del disyuntor en memoria, lo que implica que en un despliegue horizontal multi-instancia cada nodo gestionará su propio estado del circuito. Asimismo, durante fallos transitorios reales, el cliente experimentará una latencia adicional acumulada de entre 100 ms y 300 ms antes de obtener una respuesta definitiva.

**Consecuencias:**  
El servicio de Préstamos adquiere alta tolerancia a fallos y garantiza que sus respuestas de error cumplan el esquema `{codigo, mensaje}` especificado en OpenAPI 3.0. Los orquestadores de contenedores pueden detectar la indisponibilidad de dependencias mediante el endpoint `/health` estructurado.
