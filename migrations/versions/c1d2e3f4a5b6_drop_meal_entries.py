"""drop_meal_entries : la nutrition vit dans un service indépendant (/root/nutrition)

Revision ID: c1d2e3f4a5b6
Revises: 9055a9201366
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.types  # noqa: F401

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = '9055a9201366'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('idx_meal_entries_user_date', table_name='meal_entries')
    op.drop_index(op.f('ix_meal_entries_user_id'), table_name='meal_entries')
    op.drop_table('meal_entries')


def downgrade() -> None:
    """Recrée la table vide : les données sont dans le service nutrition et les sauvegardes."""
    op.create_table(
        'meal_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('entry_type', sa.String(length=16), nullable=False),
        sa.Column('meal_slot', sa.String(length=16), nullable=True),
        sa.Column('raw_description', sa.Text(), nullable=False),
        sa.Column('estimated_calories', sa.Integer(), nullable=False),
        sa.Column('created_at', app.db.types.UtcDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', app.db.types.UtcDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_meal_entries_user_date', 'meal_entries', ['user_id', 'entry_date'], unique=False)
    op.create_index(op.f('ix_meal_entries_user_id'), 'meal_entries', ['user_id'], unique=False)
