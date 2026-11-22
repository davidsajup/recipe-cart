"""create parsed_dishes cache table

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parsed_dishes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("data", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_parsed_dishes_key", "parsed_dishes", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_parsed_dishes_key", table_name="parsed_dishes")
    op.drop_table("parsed_dishes")
