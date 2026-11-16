"""stopped: an app off on purpose

Revision ID: 9e401ac07742
Revises: 52c600f64247
Create Date: 2026-11-16 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

STATUSES = ("planned", "active", "stopped", "missing", "decommissioning", "retained", "retired")


def _status_check(statuses: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{s}'" for s in statuses)
    for table in ("item", "item_history", "relation", "relation_history"):
        op.drop_constraint("status", table, type_="check")
        op.create_check_constraint("status", table, f"status IN ({allowed})")


# revision identifiers, used by Alembic.
revision: str = "9e401ac07742"
down_revision: str | Sequence[str] | None = "52c600f64247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    _status_check(STATUSES)  # by hand - autogenerate can't see CHECK constraints


def downgrade() -> None:
    """Downgrade schema."""
    _status_check(tuple(s for s in STATUSES if s != "stopped"))  # fails if any row is stopped - as it should
