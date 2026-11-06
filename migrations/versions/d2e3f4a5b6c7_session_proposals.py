"""session_proposals: sessions proposed through the agent API

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.types

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'session_proposals',
        sa.Column('id', sa.String(length=12), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('workout_type', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('duration_minutes', sa.SmallInteger(), nullable=False),
        sa.Column('tss_estimate', sa.Float(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('publication_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', app.db.types.UtcDateTime(), nullable=False),
        sa.Column('decided_at', app.db.types.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_session_proposals_user_id'), 'session_proposals', ['user_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_session_proposals_user_id'), table_name='session_proposals')
    op.drop_table('session_proposals')
