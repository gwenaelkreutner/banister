"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Custom column types used across this project's models. Autogenerate renders these by
# fully-qualified path (app.db.types.UtcDateTime(...)) but does not reliably emit the
# import that path needs — observed directly: a first-attempt baseline referenced
# app.db.types.UtcDateTime with no import of `app` at all, a NameError at migration run
# time rather than a lint nit. A plain `import app.db.types` (not `from ... import`) is
# what the dotted-path reference actually requires, and covers every future migration.
import app.db.types  # noqa: F401
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
