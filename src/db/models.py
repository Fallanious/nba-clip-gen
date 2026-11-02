"""SQLAlchemy models for nba-clip-gen."""

from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    Column, SmallInteger, BigInteger, Integer, String, Text, Date,
    Boolean, Numeric, Interval, CheckConstraint, ForeignKey,
    Index, Identity, Enum as SQLEnum, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class ClipRole(enum.Enum):
    """Enum for clip_players.role."""
    shot = "shot"
    assist = "assist"
    block = "block"
    steal = "steal"
    rebound = "rebound"
    foul = "foul"
    fouled = "fouled"
    turnover = "turnover"


class ClipStatus(enum.Enum):
    """Enum for clips.status."""
    pending = "pending"
    rendered = "rendered"
    uploaded = "uploaded"
    failed = "failed"


class Team(Base):
    """NBA teams."""
    __tablename__ = "teams"

    id = Column(SmallInteger, Identity(always=True), primary_key=True)
    abbr = Column(String(3), unique=True, nullable=False)
    city = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    created_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))

    __table_args__ = (
        CheckConstraint("abbr ~ '^[A-Z]{3}$'", name="teams_abbr_check"),
    )


class Player(Base):
    """NBA players."""
    __tablename__ = "players"

    id = Column(BigInteger, Identity(always=True), primary_key=True)
    bbref_id = Column(Text, unique=True, nullable=False)
    full_name = Column(Text, nullable=False)
    first_name = Column(Text, nullable=True)
    last_name = Column(Text, nullable=False)
    position = Column(Text, nullable=True)
    team_id = Column(SmallInteger, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    headshot_url = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    updated_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))

    team = relationship("Team", backref="players")

    __table_args__ = (
        Index("players_last_name", "last_name", "id"),
        Index("players_team", "team_id"),
        Index(
            "players_name_trgm",
            text("lower(f_unaccent(full_name)) gin_trgm_ops"),
            postgresql_using="gin"
        ),
    )


class Game(Base):
    """NBA games."""
    __tablename__ = "games"

    id = Column(BigInteger, Identity(always=True), primary_key=True)
    bbref_id = Column(Text, unique=True, nullable=False)
    game_date = Column(Date, nullable=False)
    season = Column(Text, nullable=False)
    home_team_id = Column(SmallInteger, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(SmallInteger, ForeignKey("teams.id"), nullable=False)
    s3_key = Column(Text, nullable=True)
    duration_s = Column(Numeric, nullable=True)
    created_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))

    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])

    __table_args__ = (
        CheckConstraint("home_team_id != away_team_id", name="games_different_teams_check"),
        Index("games_date", "game_date", postgresql_ops={"game_date": "DESC"}),
        Index("games_home", "home_team_id"),
        Index("games_away", "away_team_id"),
    )


class Clip(Base):
    """Video clips from games."""
    __tablename__ = "clips"

    id = Column(BigInteger, Identity(always=True), primary_key=True)
    game_id = Column(BigInteger, ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    game_date = Column(Date, nullable=False)
    play_index = Column(Integer, nullable=False)
    period = Column(SmallInteger, nullable=False)
    game_clock = Column(Interval, nullable=True)
    team_id = Column(SmallInteger, ForeignKey("teams.id"), nullable=True)
    description = Column(Text, nullable=False)
    action_type = Column(Text, nullable=True)
    shot_subtype = Column(Text, nullable=True)
    outcome = Column(Text, nullable=True)
    attributes = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    video_start_s = Column(Numeric, nullable=False)
    video_end_s = Column(Numeric, nullable=False)
    duration_s = Column(Numeric, nullable=True)
    s3_key = Column(Text, unique=True, nullable=True)
    status = Column(SQLEnum(ClipStatus, name="clip_status"), nullable=False, server_default=text("'pending'::clip_status"))
    created_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))
    updated_at = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))

    game = relationship("Game", backref="clips")
    team = relationship("Team", backref="clips")

    __table_args__ = (
        CheckConstraint("video_end_s > video_start_s AND video_start_s >= 0", name="clips_video_times_check"),
        CheckConstraint("period >= 1 AND period <= 10", name="clips_period_check"),
        Index("clips_game_period", "game_id", "period", text("game_clock DESC")),
        Index("clips_team", "team_id"),
        Index(
            "clips_status_pending",
            "status",
            postgresql_where=text("status != 'uploaded'")
        ),
    )


class ClipPlayer(Base):
    """Many-to-many relationship between clips and players with roles."""
    __tablename__ = "clip_players"

    player_id = Column(BigInteger, ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)
    clip_id = Column(BigInteger, ForeignKey("clips.id", ondelete="CASCADE"), primary_key=True)
    role = Column(SQLEnum(ClipRole, name="clip_role"), primary_key=True, nullable=False)
    game_date = Column(Date, nullable=False)

    player = relationship("Player", backref="clip_players")
    clip = relationship("Clip", backref="clip_players")

    __table_args__ = (
        Index("clip_players_player_date", "player_id", text("game_date DESC"), text("clip_id DESC")),
        Index("clip_players_clip", "clip_id"),
    )
