"""
Script para inicializar la base de datos de catalogo_service con datos de prueba.
Crea libros representativos de los géneros del modelo y sus respectivos ejemplares
(en estados DISPONIBLE y PRESTADO).
"""

import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

# Permitir ejecución tanto directa como modular
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import Base, engine, SessionLocal
from src.models import Libro, Ejemplar

# Datos iniciales con cobertura de todos los géneros especificados en Libro:
# ACCION, COMEDIA, DRAMA, SCI-FI, FILOSOFIA, TERROR, MANGA, COCINA, LITERATURA
LIBROS_SEMILLA: List[Dict[str, Any]] = [
    # SCI-FI
    {
        "titulo": "Dune",
        "autor": "Frank Herbert",
        "genero": "SCI-FI",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Fundación",
        "autor": "Isaac Asimov",
        "genero": "SCI-FI",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Neuromante",
        "autor": "William Gibson",
        "genero": "SCI-FI",
        "ejemplares": ["PRESTADO"],  # Libro sin copias disponibles inmediatamente
    },
    {
        "titulo": "Fahrenheit 451",
        "autor": "Ray Bradbury",
        "genero": "SCI-FI",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },

    # LITERATURA
    {
        "titulo": "Cien años de soledad",
        "autor": "Gabriel García Márquez",
        "genero": "LITERATURA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Don Quijote de la Mancha",
        "autor": "Miguel de Cervantes",
        "genero": "LITERATURA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Ficciones",
        "autor": "Jorge Luis Borges",
        "genero": "LITERATURA",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Rayuela",
        "autor": "Julio Cortázar",
        "genero": "LITERATURA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },

    # FILOSOFIA
    {
        "titulo": "Meditaciones",
        "autor": "Marco Aurelio",
        "genero": "FILOSOFIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Así habló Zaratustra",
        "autor": "Friedrich Nietzsche",
        "genero": "FILOSOFIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "La República",
        "autor": "Platón",
        "genero": "FILOSOFIA",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "El mito de Sísifo",
        "autor": "Albert Camus",
        "genero": "FILOSOFIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },

    # TERROR
    {
        "titulo": "El resplandor",
        "autor": "Stephen King",
        "genero": "TERROR",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Drácula",
        "autor": "Bram Stoker",
        "genero": "TERROR",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "La llamada de Cthulhu",
        "autor": "H.P. Lovecraft",
        "genero": "TERROR",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },

    # MANGA
    {
        "titulo": "Berserk Vol. 1",
        "autor": "Kentaro Miura",
        "genero": "MANGA",
        "ejemplares": ["DISPONIBLE", "PRESTADO", "PRESTADO"],
    },
    {
        "titulo": "Death Note Vol. 1",
        "autor": "Tsugumi Ohba",
        "genero": "MANGA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Monster Vol. 1",
        "autor": "Naoki Urasawa",
        "genero": "MANGA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },

    # ACCION
    {
        "titulo": "El código Da Vinci",
        "autor": "Dan Brown",
        "genero": "ACCION",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Los juegos del hambre",
        "autor": "Suzanne Collins",
        "genero": "ACCION",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "El ultimátum de Bourne",
        "autor": "Robert Ludlum",
        "genero": "ACCION",
        "ejemplares": ["PRESTADO"],
    },

    # COMEDIA
    {
        "titulo": "Guía del autoestopista galáctico",
        "autor": "Douglas Adams",
        "genero": "COMEDIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "Buenos presagios",
        "autor": "Terry Pratchett y Neil Gaiman",
        "genero": "COMEDIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Sin noticias de Gurb",
        "autor": "Eduardo Mendoza",
        "genero": "COMEDIA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },

    # DRAMA
    {
        "titulo": "Crimen y castigo",
        "autor": "Fiódor Dostoyevski",
        "genero": "DRAMA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE", "PRESTADO"],
    },
    {
        "titulo": "El gran Gatsby",
        "autor": "F. Scott Fitzgerald",
        "genero": "DRAMA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Bodas de sangre",
        "autor": "Federico García Lorca",
        "genero": "DRAMA",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },

    # COCINA
    {
        "titulo": "El arte de la cocina francesa",
        "autor": "Julia Child",
        "genero": "COCINA",
        "ejemplares": ["DISPONIBLE", "DISPONIBLE"],
    },
    {
        "titulo": "Cocina fácil para todos",
        "autor": "Karlos Arguiñano",
        "genero": "COCINA",
        "ejemplares": ["DISPONIBLE", "PRESTADO"],
    },
]


def seed_database(session: Optional[Session] = None, reset: bool = False) -> Dict[str, int]:
    """
    Puebla la base de datos con los libros y ejemplares iniciales.

    :param session: Sesión de SQLAlchemy opcional. Si no se provee, se crea una nueva.
    :param reset: Si es True, elimina los registros existentes antes de insertar.
    :return: Diccionario con el conteo de libros y ejemplares creados.
    """
    should_close = False
    if session is None:
        session = SessionLocal()
        should_close = True

    try:
        # Asegurar que las tablas existan
        Base.metadata.create_all(bind=engine)

        if reset:
            session.query(Ejemplar).delete()
            session.query(Libro).delete()
            session.commit()
            print("ℹ️  Tablas 'ejemplares' y 'libros' limpiadas exitosamente.")
        else:
            existing_count = session.query(Libro).count()
            if existing_count > 0:
                print(f"⚠️  La base de datos ya contiene {existing_count} libros. Usa reset=True o el flag --reset para recargar.")
                return {"libros": 0, "ejemplares": 0}

        total_libros = 0
        total_ejemplares = 0

        for data in LIBROS_SEMILLA:
            ejemplares_objs = [
                Ejemplar(estado=estado) for estado in data["ejemplares"]
            ]
            libro = Libro(
                titulo=data["titulo"],
                autor=data["autor"],
                genero=data["genero"],
                ejemplares=ejemplares_objs,
            )
            session.add(libro)
            total_libros += 1
            total_ejemplares += len(ejemplares_objs)

        session.commit()
        print(f"✅ Población completada con éxito: {total_libros} libros y {total_ejemplares} ejemplares insertados.")
        return {"libros": total_libros, "ejemplares": total_ejemplares}

    except Exception as e:
        session.rollback()
        print(f"❌ Error durante el seed: {e}")
        raise
    finally:
        if should_close:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Poblar la base de datos de catalogo_service con datos iniciales.")
    parser.add_argument("--reset", action="store_true", help="Elimina los datos existentes antes de poblar.")
    args = parser.parse_args()

    seed_database(reset=args.reset)


if __name__ == "__main__":
    main()
