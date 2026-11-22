"""create products table

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("pack_size", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.CheckConstraint("pack_size > 0", name="ck_products_pack_size_positive"),
        sa.CheckConstraint("unit in ('g', 'ml', 'count')", name="ck_products_unit"),
    )
    op.create_index("ix_products_name", "products", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_products_name", table_name="products")
    op.drop_table("products")
