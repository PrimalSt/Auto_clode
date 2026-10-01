"""Окружение Alembic. Миграции запускает ``SqliteMetadataStore`` при открытии базы, передавая
соединение через ``config.attributes["connection"]``; отдельная команда ``alembic`` не нужна."""

from alembic import context

from autogenerator.storage.tables import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Миграции запускаются из SqliteMetadataStore: передайте соединение в config.attributes")

# render_as_batch: SQLite не умеет большинство ALTER TABLE, Alembic пересоздаёт таблицу.
context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
