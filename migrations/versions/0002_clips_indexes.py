"""Pending DBA review: extra clips indexes and UNIQUE(game_id, play_index).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02

DO NOT RUN against any database from this PR. The UNIQUE constraint already
exists on greenfield 0001_init installs; this revision is for stamped/legacy
databases and additional filter indexes. Apply only after DBA review.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: safe if 0001 already created UNIQUE (game_id, play_index).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS clips_game_id_play_index_key
            ON clips (game_id, play_index);
        CREATE INDEX IF NOT EXISTS clips_action_type ON clips (action_type);
        CREATE INDEX IF NOT EXISTS clips_shot_subtype ON clips (shot_subtype);
        CREATE INDEX IF NOT EXISTS clips_outcome ON clips (outcome);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS clips_outcome;
        DROP INDEX IF EXISTS clips_shot_subtype;
        DROP INDEX IF EXISTS clips_action_type;
        DROP INDEX IF EXISTS clips_game_id_play_index_key;
        """
    )
