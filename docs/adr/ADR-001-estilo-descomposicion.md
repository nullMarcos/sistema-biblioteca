# ADR-001: Estilo de Integración y Descomposición del Sistema de Biblioteca

* **Estatus:** Aprobado
* **Fecha:** 2026-09-22
* **Autor:** Equipo de Desarrollo (Servicio gRPC - Catálogo)
* **Módulo:** `catalogo_service` / `contracts`

---

## 1. Contexto y Problema

El sistema de gestión de biblioteca requiere separar la lógica de negocio en servicios independientes para coordinar el trabajo del equipo:
* El servicio de **Préstamos** (API REST), encargado de la gestión de socios y préstamos.
* El servicio de **Catálogo** (gRPC), encargado del inventario de libros y disponibilidad de ejemplares.

Se requiere definir el estilo de integración y la arquitectura de comunicación entre ambos servicios.

---

## 2. Decisión de Arquitectura

Se acuerda estructurar la arquitectura bajo los siguientes pilares:

1. **Descomposición de Servicios:**
   * **`prestamos_service` (REST API):** Gestiona socios, préstamos y autenticación mediante API Key.
   * **`catalogo_service` (gRPC):** Gestiona la consulta de disponibilidad, listado de catálogo, reserva y liberación de ejemplares.

2. **Estilo de Integración y Protocolo:**
   * Integración síncrona interna mediante **gRPC** sobre HTTP/2 y **Protocol Buffers v3** (`contracts/catalogo.proto`).
   * Interfaz pública expuesta mediante **REST / JSON** (OpenAPI 3.0).

3. **Separación de Bases de Datos (T5):**
   * Cada servicio administra su propia base de datos de manera independiente (`prestamos.db` y `catalogo.db`).

4. **Funcionalidades del Servicio de Catálogo:**
   * Transacciones atómicas en reservación/liberación de ejemplares (`with_for_update`).
   * Soporte para **gRPC Reflection** e interfaz de **Health Check (`grpc.health.v1`)**.
   * Filtros de catálogo por género y disponibilidad.

---

## 3. Consecuencias

### Positivas (+)
* **Eficiencia y baja latencia:** La comunicación gRPC en formato binario optimiza las llamadas inter-servicio.
* **Contrato explícito:** El archivo `catalogo.proto` establece una interfaz fuertemente tipada entre el servicio de Préstamos y Catálogo.
* **Autonomía de datos:** Cada servicio mantiene el control exclusivo de su propia base de datos.

### Negativas / Desafíos (-)
* **Dependencia síncrona:** El servicio de Préstamos requiere que el servicio de Catálogo esté disponible para concretar reservas.
* **Control de versiones:** Los cambios en el archivo `.proto` requieren recompilar los stubs en ambos servicios.
