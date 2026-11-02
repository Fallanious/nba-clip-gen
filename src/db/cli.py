"""Database CLI commands."""

import sys
import click
from pathlib import Path
from sqlalchemy import text
from db.session import get_session
from config import config


@click.group()
def db_cli():
    """Database commands."""
    pass


@db_cli.command()
@click.argument('seed_file', type=click.Choice(['teams', 'dev_sample']))
def seed(seed_file):
    """Run a seed file."""
    if seed_file == 'dev_sample' and config.APP_ENV != 'dev':
        click.echo("Error: dev_sample can only be run in dev environment", err=True)
        sys.exit(1)

    seed_path = Path(__file__).parent / "sql" / "seed" / f"{seed_file}.sql"
    if not seed_path.exists():
        click.echo(f"Error: Seed file not found: {seed_path}", err=True)
        sys.exit(1)

    sql = seed_path.read_text(encoding="utf-8")

    try:
        with get_session() as session:
            session.execute(text(sql))
            session.commit()
        click.echo(f"Successfully ran seed: {seed_file}")
    except Exception as e:
        click.echo(f"Error running seed: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    db_cli()
