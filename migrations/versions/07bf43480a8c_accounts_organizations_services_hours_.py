"""accounts organizations services hours bookings

Revision ID: 07bf43480a8c
Revises: a4306a4b2890
Create Date: 2026-11-10 19:35:58.174048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Business tables protected by row level security (see db/tenancy.py and db.session.TENANT_ROLE).
RLS_TABLES = ("agent_profiles", "services", "business_hours", "customers", "bookings")
TENANT_ROLE = "talkbalt_app"

# revision identifiers, used by Alembic.
revision: str = '07bf43480a8c'
down_revision: Union[str, Sequence[str], None] = 'a4306a4b2890'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")  # equality on org_id inside a GiST exclusion
    op.create_table('organizations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('agent_profiles',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('business_name', sa.String(length=160), nullable=False),
    sa.Column('greeting', sa.String(length=300), nullable=True),
    sa.Column('about', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id')
    )
    op.create_table('business_hours',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('weekday', sa.SmallInteger(), nullable=False),
    sa.Column('opens', sa.Time(), nullable=False),
    sa.Column('closes', sa.Time(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id', 'weekday', 'opens')
    )
    op.create_table('customers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('phone_e164', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'phone_e164')
    )
    op.create_index(op.f('ix_customers_org_id'), 'customers', ['org_id'], unique=False)
    op.create_table('memberships',
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('org_id', 'user_id')
    )
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_table('services',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('duration_min', sa.Integer(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'name')
    )
    op.create_index(op.f('ix_services_org_id'), 'services', ['org_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('bookings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('service_id', sa.Uuid(), nullable=True),
    sa.Column('customer_id', sa.Uuid(), nullable=False),
    sa.Column('call_id', sa.Uuid(), nullable=True),
    sa.Column('service_name', sa.String(length=120), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bookings_customer_id'), 'bookings', ['customer_id'], unique=False)
    # Calendar views read one business's bookings by time.
    op.create_index('ix_bookings_org_id_starts_at', 'bookings', ['org_id', 'starts_at'], unique=False)
    op.create_check_constraint('ck_bookings_ends_after_start', 'bookings', 'ends_at > starts_at')
    # Two active bookings of one business can never overlap, even when two calls book at once.
    op.execute(
        "ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap EXCLUDE USING gist "
        "(org_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status <> 'cancelled')"
    )
    op.add_column('calls', sa.Column('org_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_calls_org_id', 'calls', 'organizations', ['org_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_calls_org_id_started_at', 'calls', ['org_id', 'started_at'], unique=False)

    # Row level security: sessions opened for one organization switch to TENANT_ROLE and set
    # app.org_id; the policies then hide and refuse every other organization's rows.
    op.execute(
        f"""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TENANT_ROLE}') THEN
                CREATE ROLE {TENANT_ROLE} NOLOGIN;
              END IF;
            END $$"""
    )
    op.execute(f"GRANT {TENANT_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {TENANT_ROLE}")
    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {TENANT_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {TENANT_ROLE} "
            "USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid) "
            "WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid)"
        )
    # Bookings reference services and calls; the tenant role only reads those for lookups.
    op.execute(f"GRANT SELECT ON organizations, calls TO {TENANT_ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"REVOKE SELECT ON organizations, calls FROM {TENANT_ROLE}")
    op.drop_index('ix_calls_org_id_started_at', table_name='calls')
    op.drop_constraint('fk_calls_org_id', 'calls', type_='foreignkey')
    op.drop_column('calls', 'org_id')
    op.drop_index('ix_bookings_org_id_starts_at', table_name='bookings')
    op.drop_index(op.f('ix_bookings_customer_id'), table_name='bookings')
    op.drop_table('bookings')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_services_org_id'), table_name='services')
    op.drop_table('services')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_customers_org_id'), table_name='customers')
    op.drop_table('customers')
    op.drop_table('business_hours')
    op.drop_table('agent_profiles')
    op.drop_table('users')
    op.drop_table('organizations')
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {TENANT_ROLE}")
