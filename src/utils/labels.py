"""Multi-attribute labeling from play-by-play descriptions.

Each clip carries an ``attributes`` dict with the following heads:

- ``action_type``:          shot | rebound | steal | block | turnover | foul |
                            free_throw | other
- ``shot_subtype``:         dunk | layup | midrange | three_pointer | hook |
                            floater | None (None unless action_type is shot or
                            free_throw)
- ``outcome``:              made | missed | None
- ``shot_distance_bucket``: at_rim | short | mid | long_two | three | None
- ``assisted``:             yes | no | None
- ``team``:                 raw team code from the play record (not normalized)

``extract_attributes`` is deterministic and consumes only the raw PBP text plus
the team code that is already present on the play record. Missing fields are
returned as ``None`` so downstream training can mask them with ``ignore_index``.
"""

import re


ATTRIBUTE_SCHEMA = {
    "action_type": [
        "shot",
        "rebound",
        "steal",
        "block",
        "turnover",
        "foul",
        "free_throw",
        "other",
    ],
    "shot_subtype": [
        "dunk",
        "layup",
        "midrange",
        "three_pointer",
        "hook",
        "floater",
    ],
    "outcome": ["made", "missed"],
    "shot_distance_bucket": ["at_rim", "short", "mid", "long_two", "three"],
    "assisted": ["yes", "no"],
}


# Ordered list of (subtype, regex) pairs. First match wins. Ordering matters:
# more specific patterns (dunk, three) come before generic ones (jump shot).
_SUBTYPE_PATTERNS = [
    ("dunk", r"\bdunk\b"),
    ("three_pointer", r"\b3-?pt\b|\bthree[- ]?point\b"),
    ("hook", r"hook shot"),
    ("floater", r"\bfloater\b|\brunner\b"),
    ("layup", r"\blayup\b|finger roll|\btip[- ]?in\b"),
    # "jump shot", "pull-up", "fade-away", "turnaround" collapse to midrange
    # unless a 3-pt indicator matched above.
    ("midrange", r"jump shot|pull.?up|fade.?away|turnaround"),
]

_ASSIST_RE = re.compile(r"\(assist by", re.IGNORECASE)
_DISTANCE_RE = re.compile(r"from\s+(\d+)\s*ft", re.IGNORECASE)
_MAKE_RE = re.compile(r"\bmakes?\b", re.IGNORECASE)
_MISS_RE = re.compile(r"\bmiss(?:es)?\b", re.IGNORECASE)


def _distance_to_bucket(feet):
    """Map shot distance in feet to a coarse bucket.

    Buckets roughly mirror common NBA shot zones. The three-point line is
    ~22 ft (corner) to 23.75 ft (arc); anything >= 23 ft is treated as a
    three-point attempt.
    """
    if feet is None:
        return None
    if feet <= 3:
        return "at_rim"
    if feet <= 10:
        return "short"
    if feet <= 16:
        return "mid"
    if feet <= 22:
        return "long_two"
    return "three"


def _detect_shot_subtype(desc_lower):
    for subtype, pattern in _SUBTYPE_PATTERNS:
        if re.search(pattern, desc_lower):
            return subtype
    return None


def extract_attributes(description, team=None):
    """Parse a PBP description into the multi-head attribute dict.

    Returns a dict with every key in ``ATTRIBUTE_SCHEMA`` plus ``team``. Fields
    that cannot be determined from the text are set to ``None``.
    """
    attrs = {
        "action_type": "other",
        "shot_subtype": None,
        "outcome": None,
        "shot_distance_bucket": None,
        "assisted": None,
        "team": team,
    }

    if not description:
        return attrs

    desc = str(description)
    desc_lower = desc.lower()

    # Outcome first — "makes"/"misses" is the strongest single signal and is
    # reused when deciding action_type for shot vs free_throw vs putback.
    if _MAKE_RE.search(desc):
        attrs["outcome"] = "made"
    elif _MISS_RE.search(desc):
        attrs["outcome"] = "missed"

    # Free throw is its own action_type so the model can learn the very
    # different visual pattern (stationary shooter at the line).
    if "free throw" in desc_lower:
        attrs["action_type"] = "free_throw"
        attrs["shot_subtype"] = None
        # Free throws don't have a meaningful distance bucket.
        return _finalize(attrs, desc_lower)

    subtype = _detect_shot_subtype(desc_lower)

    # Putback: a missed shot + immediate offensive rebound + score. The PBP
    # text looks like "... rebound by X ... makes layup/dunk". We treat it as
    # a shot with the detected subtype; the upstream "rebound" event for that
    # same play will be its own clip if one was generated.
    is_putback = bool(re.search(r"rebound.*makes.*(layup|dunk|tip)", desc_lower))

    if subtype:
        attrs["action_type"] = "shot"
        attrs["shot_subtype"] = subtype
    elif is_putback:
        attrs["action_type"] = "shot"
        attrs["shot_subtype"] = "layup" if "layup" in desc_lower else "dunk"

    # Non-shot action types. These are checked after shot detection so a
    # "blocks the shot" narration still classifies the overall clip as a
    # block rather than a generic shot; but if the clip caption is just a
    # made shot, the earlier shot branch wins.
    if attrs["action_type"] == "other":
        if re.search(r"\bblock(s|ed|ing)?\b", desc_lower):
            attrs["action_type"] = "block"
        elif re.search(r"\bsteal(s|ing)?\b", desc_lower):
            attrs["action_type"] = "steal"
        elif re.search(r"\bturnover\b|\bbad pass\b|\btraveling\b|\boffensive foul\b", desc_lower):
            attrs["action_type"] = "turnover"
        elif re.search(r"\bfoul\b", desc_lower):
            attrs["action_type"] = "foul"
        elif re.search(r"\brebound\b", desc_lower):
            attrs["action_type"] = "rebound"

    return _finalize(attrs, desc_lower)


def _finalize(attrs, desc_lower):
    """Fill in distance bucket, assisted flag, and consistency pruning."""
    # Assisted only applies to made shots.
    if attrs["action_type"] == "shot" and attrs["outcome"] == "made":
        attrs["assisted"] = "yes" if _ASSIST_RE.search(desc_lower) else "no"

    # Distance bucket only applies to shot attempts.
    if attrs["action_type"] == "shot":
        m = _DISTANCE_RE.search(desc_lower)
        if m:
            try:
                feet = int(m.group(1))
                attrs["shot_distance_bucket"] = _distance_to_bucket(feet)
            except ValueError:
                attrs["shot_distance_bucket"] = None
        # If subtype is three_pointer but no distance parsed, we can still
        # assert the bucket.
        if attrs["shot_subtype"] == "three_pointer" and attrs["shot_distance_bucket"] is None:
            attrs["shot_distance_bucket"] = "three"

    return attrs


def primary_action(attributes):
    """Return a single-string summary for filenames/compact UI display.

    Prefers the shot subtype when available (more descriptive), falling back
    to ``action_type``. This is not used as a training target; it exists so
    any remaining single-label consumer (filenames, quick CLI summaries) keeps
    working without reintroducing a flat taxonomy.
    """
    if not attributes:
        return "other"
    return attributes.get("shot_subtype") or attributes.get("action_type") or "other"
