"""Fetch NBA Stats PlayByPlayV3 events for an NBA ID or a local date/home game key."""

import argparse
import csv
import json
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

from nba_api.stats.endpoints.boxscoresummaryv3 import BoxScoreSummaryV3
from nba_api.stats.endpoints.leaguegamelog import LeagueGameLog
from nba_api.stats.endpoints.playbyplayv3 import PlayByPlayV3

from persist_matcher_clips import nba_season


def game_key(value):
    legacy = re.fullmatch(
        r"https://(?:www\.)?basketball-reference\.com/boxscores/pbp/(\d{9}[A-Z]{3})\.html",
        value,
    )
    key = legacy[1] if legacy else value
    if not re.fullmatch(r"(?:\d{10}|\d{8}0[A-Z]{3})", key):
        raise ValueError("Expected a ten-digit NBA game ID or a local date/home key")
    return key


@lru_cache(maxsize=12)
def game_log(season, phase):
    data = LeagueGameLog(
        season=season, season_type_all_star=phase, timeout=25
    ).league_game_log.get_dict()
    return [dict(zip(data["headers"], row)) for row in data["data"]]


def resolve_game(key):
    if key.isdigit():
        return key
    day = datetime.strptime(key[:8], "%Y%m%d").date().isoformat()
    home = {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"}.get(key[-3:], key[-3:])
    for phase in ("Regular Season", "Playoffs", "Pre Season", "PlayIn"):
        ids = {
            row["GAME_ID"]
            for row in game_log(nba_season(day), phase)
            if row["GAME_DATE"] == day
            and row["TEAM_ABBREVIATION"] == home
            and " vs. " in row["MATCHUP"]
        }
        if len(ids) == 1:
            return ids.pop()
        if ids:
            break
    raise ValueError(f"No unique NBA game for {key}")


def normalize_game(game, nba_id):
    if game["gameId"] != nba_id or not game["actions"]:
        raise ValueError(f"Missing or mismatched NBA game {nba_id}")
    plays = []
    score = "0-0"
    seen = set()
    for action in game["actions"]:
        if action["scoreAway"] != "" and action["scoreHome"] != "":
            score = f"{int(action['scoreAway'])}-{int(action['scoreHome'])}"
        clock = re.fullmatch(r"PT(\d+)M(\d{1,2})(\.\d+)?S", action["clock"])
        if not clock or action["actionId"] in seen:
            raise ValueError(f"Invalid clock or duplicate action in {nba_id}")
        seen.add(action["actionId"])
        fraction = (clock[3] or "").rstrip("0").rstrip(".") or ".0"
        plays.append(
            dict(
                quarter=action["period"],
                time=f"{int(clock[1])}:{int(clock[2]):02}{fraction}",
                score=score,
                team=action["teamTricode"],
                description=action["description"],
                nba_game_id=nba_id,
                nba_action_id=action["actionId"],
                action_type=action["actionType"],
                player=action["playerNameI"],
                person_id=action["personId"],
            )
        )
    return plays


def save_atomic(path, writer):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf8", newline="", dir=path.parent, delete=False
    ) as tmp:
        try:
            writer(tmp)
            tmp.flush()
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def fetch_game(key, formats="both"):
    key = game_key(key)
    nba_id = resolve_game(key)
    if key.isdigit():
        summary = BoxScoreSummaryV3(game_id=nba_id, timeout=25).game_summary.get_dict()
        (row,) = summary["data"]
        game = dict(zip(summary["headers"], row))
        code = re.fullmatch(r"(\d{8})/[A-Z]{3}([A-Z]{3})", game["gameCode"])
        if game["gameId"] != nba_id or not code:
            raise ValueError(f"Unexpected game summary for {nba_id}")
        home = {"BKN": "BRK", "CHA": "CHO", "PHX": "PHO"}.get(code[2], code[2])
        key = f"{code[1]}0{home}"
    plays = normalize_game(
        PlayByPlayV3(game_id=nba_id, timeout=25).get_dict()["game"], nba_id
    )
    base = Path("playbyplay/nba") / key
    if formats in ("both", "csv"):

        def write_csv(file):
            writer = csv.DictWriter(file, fieldnames=list(plays[0]))
            writer.writeheader()
            writer.writerows(plays)

        save_atomic(base.with_suffix(".csv"), write_csv)
    if formats in ("both", "json"):
        save_atomic(base.with_suffix(".json"), lambda f: json.dump(plays, f, indent=2))
        print(f"Saved {base}.json")
    print(f"{key} ({nba_id}): {len(plays)} NBA actions", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("games", nargs="+")
    parser.add_argument("--format", choices=("both", "json", "csv"), default="both")
    args = parser.parse_args()
    failed = []
    for value in args.games:
        try:
            fetch_game(value, args.format)
        except Exception as exc:
            print(f"{value}: {exc}", flush=True)
            failed.append(value)
    raise SystemExit(bool(failed))
