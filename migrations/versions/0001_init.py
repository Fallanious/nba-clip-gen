"""Initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-02

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from pathlib import Path

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Run the 0001_init.sql schema file."""
    schema_path = Path(__file__).parent.parent.parent / "src" / "db" / "sql" / "schema" / "0001_init.sql"
    sql = schema_path.read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Run the 0001_init.down.sql rollback file."""
    schema_path = Path(__file__).parent.parent.parent / "src" / "db" / "sql" / "schema" / "down" / "0001_init.down.sql"
    sql = schema_path.read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)
