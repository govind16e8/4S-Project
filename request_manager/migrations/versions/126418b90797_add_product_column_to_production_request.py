"""Add product column to production_request

Revision ID: 126418b90797
Revises: 16cd011c12ef
Create Date: 2025-05-27 16:29:17.218558

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '126418b90797'
down_revision = '16cd011c12ef'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('production_request', schema=None) as batch_op:
        batch_op.add_column(sa.Column('product', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('qty', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('requested_by', sa.Integer(), nullable=True))
        batch_op.alter_column('description',
               existing_type=sa.TEXT(),
               type_=sa.String(length=255),
               nullable=True)
        batch_op.drop_column('user_id')  # <-- Drop the old user_id column

def downgrade():
    with op.batch_alter_table('production_request', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.drop_column('requested_by')
        batch_op.drop_column('qty')
        batch_op.drop_column('product')
        batch_op.alter_column('description',
               existing_type=sa.String(length=255),
               type_=sa.TEXT(),
               nullable=False)