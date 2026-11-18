"""add phone"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = '0001'


def upgrade():
    op.add_column("users", sa.Column("phone", sa.Text, nullable=True))


def downgrade():
    pass
