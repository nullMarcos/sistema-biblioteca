CREATE TABLE IF NOT EXISTS reserva_operaciones (
    operacion_id VARCHAR(128) PRIMARY KEY NOT NULL,
    libro_id INTEGER NOT NULL,
    ejemplar_id INTEGER,
    estado VARCHAR(20) NOT NULL,
    motivo TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_activa_ejemplar
    ON reserva_operaciones(ejemplar_id) WHERE estado = 'RESERVADA';
CREATE TABLE IF NOT EXISTS liberacion_operaciones (
    operacion_id VARCHAR(128) PRIMARY KEY NOT NULL,
    payload_json TEXT NOT NULL,
    estado VARCHAR(20) NOT NULL
);
