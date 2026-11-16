import sqlite3

import pytest
from scripts.migrar_sqlite import aplicar


def test_migracion_incremental_una_vez_y_con_datos_previos(tmp_path):
    base = tmp_path / "existente.db"
    with sqlite3.connect(base) as db:
        db.execute("CREATE TABLE datos (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO datos VALUES (7)")
    carpeta = tmp_path / "migraciones"
    carpeta.mkdir()
    archivo = carpeta / "0001_ampliar.sql"
    archivo.write_text("ALTER TABLE datos ADD COLUMN nota TEXT; INSERT INTO datos (id) VALUES (8);")
    aplicar(base, carpeta)
    aplicar(base, carpeta)
    with sqlite3.connect(base) as db:
        assert db.execute("SELECT id, nota FROM datos ORDER BY id").fetchall() == [(7, None), (8, None)]
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone() == (1,)
    archivo.write_text("DROP TABLE datos;")
    with pytest.raises(ValueError, match="fue modificada"):
        aplicar(base, carpeta)


def test_fallo_sql_no_deja_cambios_parciales(tmp_path):
    base = tmp_path / "existente.db"
    with sqlite3.connect(base) as db:
        db.execute("CREATE TABLE datos (id INTEGER)")
        db.execute("INSERT INTO datos VALUES (7)")
    carpeta = tmp_path / "migraciones"
    carpeta.mkdir()
    (carpeta / "0001_fallida.sql").write_text("INSERT INTO datos VALUES (8); INSERT INTO tabla_inexistente VALUES (9);")
    with pytest.raises(sqlite3.OperationalError):
        aplicar(base, carpeta)
    with sqlite3.connect(base) as db:
        assert db.execute("SELECT id FROM datos").fetchall() == [(7,)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchall() == []


def test_migracion_no_puede_confirmar_cambios_antes_de_un_fallo(tmp_path):
    base = tmp_path / "existente.db"
    with sqlite3.connect(base) as db:
        db.execute("CREATE TABLE datos (id INTEGER)")
        db.execute("INSERT INTO datos VALUES (7)")
    carpeta = tmp_path / "migraciones"
    carpeta.mkdir()
    (carpeta / "0001_transaccion_invalida.sql").write_text(
        "INSERT INTO datos VALUES (8); COMMIT; INSERT INTO tabla_inexistente VALUES (9);"
    )
    with pytest.raises(sqlite3.DatabaseError):
        aplicar(base, carpeta)
    with sqlite3.connect(base) as db:
        assert db.execute("SELECT id FROM datos").fetchall() == [(7,)]
