-- Bootstrap del esquema anterior: permite aplicar el mismo historial en una BD nueva.
CREATE TABLE IF NOT EXISTS socios (
    id INTEGER PRIMARY KEY AUTOINCREMENT, rut VARCHAR(12) NOT NULL UNIQUE,
    nombre VARCHAR(255) NOT NULL, correo VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS prestamos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, socio_id INTEGER NOT NULL REFERENCES socios(id),
    libro_id INTEGER NOT NULL, ejemplar_id INTEGER NOT NULL,
    fecha_prestamo DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_limite DATE NOT NULL, fecha_devolucion DATETIME, estado VARCHAR(20) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_socio_prestamo_activo ON prestamos(socio_id, estado);
CREATE TABLE IF NOT EXISTS saga_transacciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT, saga_id VARCHAR(36) NOT NULL UNIQUE,
    idempotency_key VARCHAR(64) UNIQUE, socio_id INTEGER NOT NULL, libro_id INTEGER NOT NULL,
    ejemplar_id INTEGER, prestamo_id INTEGER, estado VARCHAR(30) NOT NULL,
    paso_actual VARCHAR(50) NOT NULL, respuesta_json TEXT, error_detalle TEXT,
    fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_saga_transacciones_saga_id ON saga_transacciones(saga_id);
CREATE INDEX IF NOT EXISTS idx_saga_estado ON saga_transacciones(estado);
ALTER TABLE saga_transacciones ADD COLUMN payload_json TEXT;
ALTER TABLE saga_transacciones ADD COLUMN actor_id VARCHAR(128);
ALTER TABLE saga_transacciones ADD COLUMN cupo_reservado BOOLEAN NOT NULL DEFAULT 0;
UPDATE saga_transacciones SET cupo_reservado = 1
    WHERE estado NOT IN ('COMPLETADA', 'COMPENSADA', 'FALLIDA');
