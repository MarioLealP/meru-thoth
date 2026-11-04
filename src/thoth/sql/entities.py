"""Every database-side entity, in the order Alembic gets them."""

from thoth.sql import grants, history

ENTITIES = [*history.ENTITIES, *grants.ENTITIES]
