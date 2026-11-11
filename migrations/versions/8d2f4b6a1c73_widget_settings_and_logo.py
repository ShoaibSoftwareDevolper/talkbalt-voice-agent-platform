"""widget settings and logo

Revision ID: 8d2f4b6a1c73
Revises: 5b1e7c2d9a40
Create Date: 2026-11-11 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8d2f4b6a1c73'
down_revision: Union[str, Sequence[str], None] = '5b1e7c2d9a40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Server defaults fill the rows of businesses that signed up before these columns existed.
    op.add_column('agent_profiles', sa.Column('widget_label', sa.String(length=40), nullable=False, server_default='Start your call'))
    op.add_column('agent_profiles', sa.Column('widget_color', sa.String(length=7), nullable=False, server_default='#4b46d6'))
    op.add_column('agent_profiles', sa.Column('widget_position', sa.String(length=5), nullable=False, server_default='right'))
    op.add_column('agent_profiles', sa.Column('logo_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_profiles', 'logo_url')
    op.drop_column('agent_profiles', 'widget_position')
    op.drop_column('agent_profiles', 'widget_color')
    op.drop_column('agent_profiles', 'widget_label')
