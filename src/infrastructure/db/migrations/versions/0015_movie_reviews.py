"""текстовые отзывы зрителей к фильмам + необязательный балл

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cinema_ratings") as batch_op:
        # отзыв зрителя (может быть без оценки-цифры)
        batch_op.add_column(sa.Column("review", sa.Text(), nullable=True))
        # балл теперь необязателен: можно оставить только текст
        batch_op.alter_column("score", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("cinema_ratings") as batch_op:
        batch_op.alter_column("score", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("review")
