"""business info, products and public calls

Revision ID: 2259f4a579d1
Revises: 07bf43480a8c
Create Date: 2026-11-10 19:55:53.680361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2259f4a579d1'
down_revision: Union[str, Sequence[str], None] = '07bf43480a8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Server defaults fill the rows of businesses that signed up before these columns existed.
    op.add_column('agent_profiles', sa.Column('location_type', sa.String(length=16), nullable=False, server_default='in_person'))
    op.add_column('agent_profiles', sa.Column('address', sa.String(length=300), nullable=True))
    op.add_column('agent_profiles', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('agent_profiles', sa.Column('country', sa.String(length=80), nullable=True))
    op.add_column('agent_profiles', sa.Column('contact_email', sa.String(length=254), nullable=True))
    op.add_column('agent_profiles', sa.Column('website', sa.String(length=300), nullable=True))
    op.add_column('agent_profiles', sa.Column('faqs', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column('agent_profiles', sa.Column('public_calls_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('services', sa.Column('kind', sa.String(length=16), nullable=False, server_default='service'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('services', 'kind')
    op.drop_column('agent_profiles', 'public_calls_enabled')
    op.drop_column('agent_profiles', 'faqs')
    op.drop_column('agent_profiles', 'website')
    op.drop_column('agent_profiles', 'contact_email')
    op.drop_column('agent_profiles', 'country')
    op.drop_column('agent_profiles', 'city')
    op.drop_column('agent_profiles', 'address')
    op.drop_column('agent_profiles', 'location_type')
