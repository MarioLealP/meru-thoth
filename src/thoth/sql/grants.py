"""What the running app's role, thoth_app, may do (D58): read, insert and update the live tables, read history.

It owns nothing, so it can't change the schema or switch off a trigger; it has no DELETE (only Charon's purge
deletes); and history is written only by the change-tracking function, which runs as the tables' owner. Migrations
run as that owner. Columns come from the models, so a new column gets its table's rights through autogenerate; a new
table gets none until it's listed here (tests/test_roles.py checks the whole matrix).
"""

from alembic_utils.pg_grant_table import PGGrantTable
from sqlmodel import SQLModel

from thoth.models import (
    Item,
    ItemHistory,
    ItemObservation,
    ItemType,
    Relation,
    RelationHistory,
    RelationObservation,
    RelationType,
    SnapshotRun,
    table_of,
)

APP_ROLE = "thoth_app"

READ_WRITE: list[type[SQLModel]] = [
    Item,
    Relation,
    ItemObservation,
    RelationObservation,
    SnapshotRun,
    ItemType,
    RelationType,
]
READ: list[type[SQLModel]] = [ItemHistory, RelationHistory]


def _grants(model: type[SQLModel], *privileges: str) -> list[PGGrantTable]:
    table = table_of(model)
    columns = sorted(column.name for column in table.columns)
    return [
        PGGrantTable(schema="public", table=table.name, columns=columns, role=APP_ROLE, grant=privilege)
        for privilege in privileges
    ]


ENTITIES = [
    *(grant for model in READ_WRITE for grant in _grants(model, "SELECT", "INSERT", "UPDATE")),
    *(grant for model in READ for grant in _grants(model, "SELECT")),
    # the migration record, so checks can read which version the database is at
    PGGrantTable(schema="public", table="alembic_version", columns=["version_num"], role=APP_ROLE, grant="SELECT"),
]
