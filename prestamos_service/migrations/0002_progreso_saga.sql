ALTER TABLE saga_transacciones ADD COLUMN reserva_id VARCHAR(128);
ALTER TABLE saga_transacciones ADD COLUMN compensacion_id VARCHAR(128);
ALTER TABLE saga_transacciones ADD COLUMN accion_pendiente VARCHAR(50);
ALTER TABLE saga_transacciones ADD COLUMN sanciones_json TEXT;
ALTER TABLE saga_transacciones ADD COLUMN lease_owner VARCHAR(36);
ALTER TABLE saga_transacciones ADD COLUMN lease_until FLOAT NOT NULL DEFAULT 0;
ALTER TABLE saga_transacciones ADD COLUMN recuperacion_intentos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE saga_transacciones ADD COLUMN proximo_intento FLOAT NOT NULL DEFAULT 0;
-- Las Sagas anteriores no enviaban identidad al Catálogo: no inventar propiedad.
UPDATE saga_transacciones SET accion_pendiente = 'CONCILIACION_LEGACY'
    WHERE estado NOT IN ('COMPLETADA', 'COMPENSADA', 'FALLIDA');
