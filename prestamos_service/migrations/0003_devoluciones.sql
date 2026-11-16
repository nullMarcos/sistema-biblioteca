CREATE TABLE devolucion_operaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operacion_id VARCHAR(36) NOT NULL UNIQUE,
    idempotency_key VARCHAR(64) NOT NULL UNIQUE,
    prestamo_id INTEGER NOT NULL UNIQUE REFERENCES prestamos(id),
    actor_id VARCHAR(128), reserva_id VARCHAR(128) NOT NULL, liberacion_id VARCHAR(128) NOT NULL,
    estado_final VARCHAR(20) NOT NULL, estado VARCHAR(30) NOT NULL,
    accion_pendiente VARCHAR(50), respuesta_json TEXT, error_detalle TEXT,
    lease_owner VARCHAR(36), lease_until FLOAT NOT NULL DEFAULT 0,
    recuperacion_intentos INTEGER NOT NULL DEFAULT 0, proximo_intento FLOAT NOT NULL DEFAULT 0,
    fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
