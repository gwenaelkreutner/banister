"""agent interface: plan change proposals and message outbox

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.types

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan_change_proposals',
        sa.Column('id', sa.String(length=12), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('based_on_plan_id', sa.Uuid(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', app.db.types.UtcDateTime(), nullable=False),
        sa.Column('decided_at', app.db.types.UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_plan_change_proposals_user_id'), 'plan_change_proposals', ['user_id'])
    op.create_table(
        'outbox_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('parse_mode', sa.String(length=16), nullable=True),
        sa.Column('silent', sa.Boolean(), nullable=False),
        sa.Column('buttons', sa.JSON(), nullable=True),
        sa.Column('agent_prompt', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', app.db.types.UtcDateTime(), nullable=False),
        sa.Column('delivered_at', app.db.types.UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_outbox_messages_status'), 'outbox_messages', ['status'])


def downgrade() -> None:
    op.drop_index(op.f('ix_outbox_messages_status'), table_name='outbox_messages')
    op.drop_table('outbox_messages')
    op.drop_index(op.f('ix_plan_change_proposals_user_id'), table_name='plan_change_proposals')
    op.drop_table('plan_change_proposals')
