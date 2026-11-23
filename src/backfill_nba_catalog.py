"""Reconcile existing catalog events to NBA actions, preserving clip IDs and footage anchors."""

import argparse
import json
from collections import Counter, defaultdict
import os
from pathlib import Path
import re
import unicodedata

from dotenv import load_dotenv
from nba_api.stats.static.teams import get_teams

from match_play_timestamps import load_pbp_source
from persist_matcher_clips import (
    assert_persist_ready,
    direct_database_url,
    game_clock_interval,
)

TEAMS = {t["nickname"].lower(): t["abbreviation"] for t in get_teams()}


def name(text):
    text = {"H. González Peña": "H. González"}.get(text, text)
    text = re.sub(r"\s+(jr\.?|sr\.?|ii|iii|iv)$", "", text, flags=re.I)
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if c.isalnum())


def event_signature(play):
    description = play["description"]
    if description == "Team misses heave shot":
        return "Heave", "Team", None
    shot = re.match(r"(.+?) (makes|misses) (.+)", description)
    if shot:
        actor, outcome, kind = shot.groups()
        if "free throw" in kind:
            attempt = re.search(r"\d of \d", kind)
            return (
                "Free Throw",
                actor,
                (attempt[0] if attempt else "", outcome == "misses"),
            )
        return (
            ("Made Shot" if outcome == "makes" else "Missed Shot"),
            actor,
            "3-pt" in kind,
        )
    sub = description.partition(" enters the game for ")
    if sub[1]:
        return "Substitution", sub[2], sub[0].partition(". ")[2]
    for word, family in (
        ("rebound", "Rebound"),
        ("foul", "Foul"),
        ("turnover", "Turnover"),
        ("violation", "Violation"),
    ):
        if word in description.partition(" by ")[0].lower() and " by " in description:
            return family, description.split(" by ", 1)[1].split(" (")[0], None
    if "timeout" in description.lower():
        return "Timeout", "Team", None
    if description.startswith("Instant Replay"):
        return "Instant Replay", "", None
    return None


def matches_event(old, new, signature):
    family, actor, detail = signature
    if family != new["action_type"]:
        return False
    text = new["description"]
    if family != "Instant Replay":
        team = TEAMS.get(old["team"].lower())
        if new["team"]:
            if family != "Foul" and team != new["team"]:
                return False
        elif not text.lower().startswith(old["team"].lower() + " "):
            return False
    if family == "Substitution":
        return name(detail) == name(text.removeprefix("SUB: ").split(" FOR ")[0])
    if family == "Violation" and actor == "Team" and old["time"] == new["time"]:
        return any(
            kind in old["description"].lower() and kind in text.lower()
            for kind in ("kicked ball", "delay of game")
        )
    if actor == "Team" and new["player"]:
        return False
    if actor != "Team" and name(actor) != name(new["player"]):
        return False
    if family == "Free Throw":
        return detail[0] in text and detail[1] == text.startswith("MISS")
    if family in ("Made Shot", "Missed Shot"):
        return detail == ("3PT" in text)
    return True


def align(old, new):
    """Accept only mutually unique candidates within the period, score and clock window."""
    by_context = defaultdict(list)
    for j, p in enumerate(new):
        by_context[p["quarter"]].append(j)
    candidates = {}
    for i, p in enumerate(old):
        sig = event_signature(p)
        if not sig:
            continue
        clock = game_clock_interval(p["time"])
        matches = [
            j
            for j in by_context[p["quarter"]]
            if (
                new[j]["score"] == p["score"]
                or (
                    new[j]["time"] == p["time"]
                    and sig[0] in ("Instant Replay", "Substitution")
                )
            )
            and abs((game_clock_interval(new[j]["time"]) - clock).total_seconds())
            <= (30 if sig[0] == "Substitution" else 6)
            and matches_event(p, new[j], sig)
        ]
        exact = [j for j in matches if new[j]["time"] == p["time"]]
        candidates[i] = exact or matches
    owners = Counter(j for matches in candidates.values() for j in matches)
    result = {
        i: js[0] for i, js in candidates.items() if len(js) == 1 and owners[js[0]] == 1
    }

    # A duplicated legacy timeout can be distinguished when it sits directly
    # between two already verified neighbors in both complete event streams.
    for i, js in candidates.items():
        if len(js) != 1 or i in result or js[0] in result.values():
            continue
        j = js[0]
        if result.get(i - 1) == j - 1 and result.get(i + 1) == j + 1:
            result[i] = j
    return result


def score(value):
    away, home = map(int, value.split("-"))
    return {"away": away, "home": home}


def reviewed_window(key, clip_id, event, meta, start, end, reviews):
    review = reviews.get(str(clip_id))
    if not review:
        return start, end
    identity = key, event["nba_action_id"], event["time"]
    window = review.get("window", review["previous_window"])
    if (
        identity != (review["game"], review["action"], review["clock"])
        or not 0 <= window[0] < window[1]
    ):
        raise ValueError(f"{clip_id}: invalid footage review")
    current = [float(start), float(end)]
    if current == window and meta.get("video_timing_review") == review["status"]:
        return start, end
    if current != review["previous_window"]:
        raise ValueError(f"{clip_id}: footage review no longer matches catalog")
    meta["video_timing_review"] = review["status"]
    return window


def plan_game(key, rows, source_dir, reviews):
    from persist_matcher_clips import canonical_group_indexes, grouping_sha256

    new_hash, new = load_pbp_source(source_dir / "nba" / f"{key}.json")
    grouping = grouping_sha256(canonical_group_indexes(new))
    mapping = None
    updates = []
    for clip_id, index, period, clock, desc, attrs, start, end in rows:
        if attrs.get("pbp_sha256") == new_hash:
            if str(clip_id) in reviews:
                meta = dict(attrs)
                bounds = reviewed_window(
                    key, clip_id, new[index], meta, start, end, reviews
                )
                if meta != attrs or tuple(bounds) != (start, end):
                    updates.append((index, desc, clock, meta, *bounds, clip_id))
            continue
        if attrs.get("nba_unmatched_for") == new_hash:
            continue
        if mapping is None:
            old_hash, old = load_pbp_source(source_dir / f"{key}.json")
            mapping = align(old, new)
        if not 0 <= index < len(old) or attrs.get("pbp_sha256") != old_hash:
            raise ValueError(f"{key} clip {clip_id}: unknown event source")
        p = old[index]
        if (period, clock, desc) != (
            p["quarter"],
            game_clock_interval(p["time"]),
            p["description"],
        ):
            raise ValueError(
                f"{key} clip {clip_id}: stored event differs from frozen source"
            )
        if index not in mapping:
            # Keep the row and its legacy provenance. Negative indexes cannot
            # collide with any current or future zero-based NBA event index.
            updates.append(
                (
                    -1 - index,
                    desc,
                    clock,
                    dict(attrs, nba_unmatched_for=new_hash, legacy_play_index=index),
                    start,
                    end,
                    clip_id,
                )
            )
            continue
        j = mapping[index]
        event = new[j]
        meta = dict(
            attrs,
            pbp_sha256=new_hash,
            grouping_sha256=grouping,
            nba_game_id=event["nba_game_id"],
            nba_action_id=event["nba_action_id"],
            game_clock=event["time"],
            period=event["quarter"],
            pre_play_score=score(new[j - 1]["score"] if j else "0-0"),
            post_play_score=score(event["score"]),
            legacy_event=dict(
                play_index=index,
                description=desc,
                time=p["time"],
                attributes=attrs,
                video_start_s=str(start),
                video_end_s=str(end),
            ),
        )
        meta["shared_sequence_indexes"] = [
            mapping[i] for i in attrs["shared_sequence_indexes"] if i in mapping
        ]
        if clock != game_clock_interval(event["time"]):
            meta["video_timing_review"] = "needs_footage_review"
        start, end = reviewed_window(key, clip_id, event, meta, start, end, reviews)
        updates.append(
            (
                j,
                event["description"],
                game_clock_interval(event["time"]),
                meta,
                start,
                end,
                clip_id,
            )
        )
    return updates


def main():
    import psycopg
    from psycopg.types.json import Jsonb

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("playbyplay"))
    parser.add_argument("--footage-review", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    reviews = json.loads(args.footage_review.read_text()) if args.footage_review else {}
    load_dotenv()
    params = assert_persist_ready(direct_database_url(os.environ["DATABASE_URL"]))
    counts = Counter()
    with psycopg.connect(**params) as conn:
        games = conn.execute(
            "SELECT id, bbref_id FROM games WHERE EXISTS "
            "(SELECT 1 FROM clips WHERE game_id=games.id) ORDER BY id FOR UPDATE"
        ).fetchall()
        for game_id, key in games:
            rows = conn.execute(
                "SELECT id,play_index,period,game_clock,description,attributes,"
                "video_start_s,video_end_s FROM clips WHERE game_id=%s FOR UPDATE",
                (game_id,),
            ).fetchall()
            updates = plan_game(key, rows, args.source_dir, reviews)
            counts["already_processed"] += len(rows) - len(updates)
            counts["matched"] += sum(u[0] >= 0 for u in updates)
            counts["unmatched"] += sum(u[0] < 0 for u in updates)
            if args.apply and updates:
                # Move all affected keys out of the way before reindexing.
                offset = (
                    max(abs(r[1]) for r in rows) + max(abs(u[0]) for u in updates) + 2
                )
                with conn.cursor() as cur:
                    cur.executemany(
                        "UPDATE clips SET play_index=%s WHERE id=%s",
                        [(offset + i, u[-1]) for i, u in enumerate(updates)],
                    )
                    cur.executemany(
                        "UPDATE clips SET play_index=%s, description=%s, game_clock=%s,"
                        "attributes=%s, video_start_s=%s, video_end_s=%s, updated_at=now() WHERE id=%s",
                        [
                            (idx, d, t, Jsonb(a), s, e, c)
                            for idx, d, t, a, s, e, c in updates
                        ],
                    )
        print("Applied" if args.apply else "Preview", dict(counts))


if __name__ == "__main__":
    main()
