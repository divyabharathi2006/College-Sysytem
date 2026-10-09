import os
from urllib.parse import unquote

from sqlalchemy import inspect

from extensions import db


def registry_table_ready(bind_key, table_name):
    engine = db.engines[bind_key]
    database = engine.url.database
    if (
        engine.url.get_backend_name() == 'sqlite'
        and database not in (None, '', ':memory:')
        and not os.path.isfile(os.path.realpath(unquote(database)))
    ):
        return False
    return inspect(engine).has_table(table_name)


def initialize_registry_databases():
    """Create missing registry tables after an explicit operator action."""
    registry_binds = (
        'university_students', 'affiliated_students', 'affiliated_colleges',
    )
    engines = [db.engine, *(db.engines[key] for key in registry_binds)]
    if any(
        engine.url.get_backend_name() != 'sqlite'
        or engine.url.database in (None, '', ':memory:')
        for engine in engines
    ):
        raise ValueError(
            'The attendance and registry databases must use file-backed SQLite URLs.'
        )

    paths = [
        os.path.normcase(os.path.realpath(unquote(engine.url.database)))
        for engine in engines
    ]
    if len(set(paths)) != len(paths):
        raise ValueError(
            'Each registry database must use a different URL from the attendance database and from each other.'
        )

    for bind_key in registry_binds:
        engine = db.engines[bind_key]
        registry_path = os.path.realpath(unquote(engine.url.database))
        os.makedirs(os.path.dirname(registry_path) or os.getcwd(), exist_ok=True)
        db.create_all(bind_key=[bind_key])
        if not inspect(engine).get_table_names():
            raise RuntimeError(f'Registry schema initialization failed for {bind_key}.')
