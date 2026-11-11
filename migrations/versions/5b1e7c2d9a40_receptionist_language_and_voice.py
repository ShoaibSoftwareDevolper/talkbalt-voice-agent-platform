"""receptionist language and voice

Revision ID: 5b1e7c2d9a40
Revises: 2259f4a579d1
Create Date: 2026-11-11 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5b1e7c2d9a40'
down_revision: Union[str, Sequence[str], None] = '2259f4a579d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Server defaults fill the rows of businesses that signed up before these columns existed.
    op.add_column('agent_profiles', sa.Column('language', sa.String(length=2), nullable=False, server_default='en'))
    op.add_column('agent_profiles', sa.Column('voice', sa.String(length=32), nullable=False, server_default='kiara'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_profiles', 'voice')
    op.drop_column('agent_profiles', 'language')
