#!/usr/bin/env python3
"""Backfill the multi-attribute label dict onto existing clips_metadata.json files.

Re-parses each clip's ``description`` (and ``team``) with ``extract_attributes``
and writes an ``attributes`` dict onto every clip record in place. Also removes
now-dead single-label keys (``label``, ``symlink_path``, ``organized_by_label``)
so the file schema matches what ``generate_clips.py`` now emits.

Idempotent — safe to re-run whenever ``extract_attributes`` changes. Originals
are left in place; no backup files are written (the JSON is regenerable from
``description``, which is the source of truth).
"""

import argparse
import json
import os
import sys
from collections import Counter
from glob import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.labels import ATTRIBUTE_SCHEMA, extract_attributes, primary_action


DEAD_CLIP_KEYS = ("label", "symlink_path")
DEAD_META_KEYS = ("organized_by_label",)


def backfill_file(path, dry_run=False):
    with open(path) as f:
        meta = json.load(f)

    clips = meta.get("clips", [])
    updated = 0
    for clip in clips:
        desc = clip.get("description", "")
        team = clip.get("team")
        clip["attributes"] = extract_attributes(desc, team)
        clip["primary"] = primary_action(clip["attributes"])
        for k in DEAD_CLIP_KEYS:
            clip.pop(k, None)
        updated += 1

    for k in DEAD_META_KEYS:
        meta.pop(k, None)

    if not dry_run:
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)

    return clips, updated


def main():
    parser = argparse.ArgumentParser(description="Backfill multi-attribute labels on clips_metadata.json files")
    parser.add_argument("--glob", default="output/clips/*/clips_metadata.json", help="Glob for metadata files")
    parser.add_argument("--dry-run", action="store_true", help="Parse and summarize without writing")
    args = parser.parse_args()

    files = sorted(glob(args.glob))
    if not files:
        print(f"No metadata files matched {args.glob}")
        return 1

    totals = {head: Counter() for head in list(ATTRIBUTE_SCHEMA.keys()) + ["team"]}
    total_clips = 0

    for path in files:
        clips, updated = backfill_file(path, dry_run=args.dry_run)
        total_clips += updated
        for clip in clips:
            attrs = clip.get("attributes", {})
            for head, counter in totals.items():
                val = attrs.get(head)
                counter[val if val is not None else "__none__"] += 1
        print(f"{'(dry) ' if args.dry_run else ''}backfilled {updated} clips: {path}")

    print("\n" + "=" * 70)
    print(f"Total clips processed: {total_clips} across {len(files)} files")
    print("=" * 70)
    for head, counter in totals.items():
        print(f"\n{head}:")
        for val, count in sorted(counter.items(), key=lambda x: -x[1]):
            print(f"  {val}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
