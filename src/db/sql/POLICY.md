# Play capability policy (slice A)

Units and types, bound policy, provenance, and rollback.
Fictional fixtures only. No live Neon. No media access.
This slice is schema, capability, migrations, and tests only.

## Units and types

| Field | Unit | SQL type | Notes |
| --- | --- | --- | --- |
| `clips.video_start_s` | seconds from the start of the original source file | `numeric` | Video-relative. Not game clock. |
| `clips.video_end_s` | seconds | `numeric` | Must be greater than start when set. |
| `clips.video_timestamp_s` | seconds | `numeric` | Matcher frame time that produced the match. |
| `games.duration_s` | seconds | `numeric` | **Trusted duration**: probe of the original private object at import. Never from the client. Never from game clock. |
| `clips.game_clock` | remaining period time | `interval` | Display only. **Never** used for eligibility. |

Application validation rejects `bool`, `str`, `None` (when required), NaN, and ±Inf.
It does **not** coerce (`True` is not `1`; `"1.5"` is not `1.5`). PostgreSQL `numeric`
cannot preserve a Python `bool` after bind; the app is the type gate.

## Security bound policy (explicit, not hidden coercion)

These limits are the documented Security policy. Values outside them are
**denied**, not clamped.

1. Finite: not NULL (when attested), not NaN, not ±Inf.
   PostgreSQL `numeric` NaN **equals itself** (`NaN = NaN` is TRUE) and sorts
   **above** every finite value, including Infinity. Do **not** use `x = x` to
   reject NaN. SQL excludes `'NaN'::numeric`, `'Infinity'::numeric`, and
   `'-Infinity'::numeric` via `nba_numeric_is_finite`. Trusted duration must
   also be **positive**. There is no `1e9` storage cap.
2. Order: `0 <= start < end`. Tiny positive windows are valid. There is **no**
   minimum clip length.
3. Zero start is valid (`start = 0` is allowed).
4. Fractional seconds are valid (`numeric`, not integer).
5. Trusted duration: positive finite `games.duration_s` from a probe of the
   original private object. `end <= duration_s` and `start < duration_s`.
   NULL, NaN, ±Inf, zero, and negative duration are unavailable. Do not guess.
6. Clip-length ceiling: `end - start <= 120` seconds (Security).

Constants (must match SQL VIEW, CHECKs, and Python):

- `CLIP_MAX_DURATION_S = 120`

## Source identity

Eligibility requires an **original private** object reference:

- `games.s3_key` non-empty, not a URL (`http(s)://` or `s3://`)
- `games.source_kind = original_private`
- `games.source_version` non-empty (ETag or VersionId of **that** object)

`public` and `derived` kinds never substitute. A public URL column is not
consulted because it is not modeled.

## Fixtures never grant live media

Fictional `can_create` true is a **fixture-only** case: `is_fictional is True`
and `s3_key` starts with `fixtures/`. Future live S3 (`games/` / `clips/`
objects) must **always deny** fixture keys. `can_create` may be true on
fictional fixtures so schema/UI can exercise eligibility. That is **not**
live media access.

`live_media_access_permitted` is independent of `can_create` and requires
`is_fictional is False` **exactly**. Unknown, missing, and bool-ish values
deny. It is also false when:

- `s3_key` starts with `fixtures/`, or
- the key is a URL, or
- the key is not a live `games/` or `clips/` object with original-private identity

A later fixture runner, if enabled, must call that helper before any bucket
access. Fixture keys cannot be rewritten onto live prefixes. CHECK
`games_fictional_key_prefix` rejects `fixtures/` on non-fictional rows and
rejects live prefixes on fictional rows. Seed files that contain `games/` or
`clips/` object literals are refused.

Prefix `games/` and `clips/` are reserved for live objects.

## Provenance (`timing_provenance`)

| Value | Meaning | Eligible? |
| --- | --- | --- |
| `none` | PBP row. Offsets NULL. Matcher did not attest. | No (`footage_not_matched`) |
| `game_clock_derived` | Numbers from period/clock. Forbidden. | No (`video_timing_unavailable`) |
| `unvalidated_numeric` | Numbers without matcher attestation (legacy). | No (`video_timing_unavailable`) |
| `matcher_attested` | Import from matcher JSON with run id, version, video timestamp, video-relative start/end. | Only if bounds + source binding also pass |

**Validated** means `matcher_attested` **and** finite video-relative bounds
**and** `clips.matched_source_version = games.source_version`.

## Stale source

If the original object is replaced, `games.source_version` changes.
Plays whose `matched_source_version` differs are `video_timing_unavailable`.
Do not keep serving a window that belonged to a different file.

## Capability shape (public)

```json
{ "can_create": false, "reason": "footage_not_matched" }
```

`reason` is `null` only when `can_create` is true.
Allowed reason strings: `footage_not_matched`, `video_timing_unavailable`.
Never echo keys, URLs, versions, offsets, or internal refs.

`can_create` is **not a grant** and **not live media access**.
Browse may show ineligible rows. This slice does not register create, status,
or playback routes.

**Published / deleted predicates are missing** from capability VIEW SQL and
browse SQL. They stay **unwired**. Later HTTP must explicitly enforce
published AND not deleted. Do not treat this slice as that gate.

Browse serializers use a **structured public-field allowlist**. Extra keys
are rejected. Prefix/value scanning is an incomplete canary, **not** secrecy,
and does not make allowlisted play text a URL filter.

## Reason precedence (first match)

1. Missing original private source ref or source version → `footage_not_matched`
2. `timing_provenance = none` (unmatched / empty timing) → `footage_not_matched`
3. `game_clock_derived` or `unvalidated_numeric` → `video_timing_unavailable`
4. Missing/invalid/out-of-range/stale video-relative window → `video_timing_unavailable`
5. Else eligible (`reason` null)

Game clock does not rescue missing timing and does not alter a valid result.

## Access policy (anonymous shared catalog)

Supersedes the earlier signed-in-required note. Identity is **not** a blocker.

**App-level policy (intentional):** anonymous callers share the full catalog
and may browse, create, and play **eligible** clips. There is **no** sign-in
provider for now. There are **no** user- or team-ownership filters.
Allowed public cases do **not** require cookies or credentials.

This is an **explicit public-resource policy**. It is **not** no-op authorize
and **not** stub identity. Do not create fake authenticated users, `LocalUser`,
scopes, or owner IDs. Do not add `owner_id` / `user_id` / org ACL columns.
Do not register `before_request_hook` to invent `g.user`.

This slice does **not** change `src/auth.py` or `src/authz.py`. Existing
helpers stay unused. Existing `/api/generate` is still out of scope.
Routes stay unwired. Fictional fixtures only. No live DB.

Offline matcher persistence is independent of this access policy and of HTTP.

## Later gates (not this slice; no job/limiter implementation here)

Create, status, playback, worker execution, and URL issuance (including
cache/304) must all re-check, server-side:

1. Resource is **published** and **not deleted**.
2. Canonical **play** is server-resolved (no client-supplied source identity).
3. Canonical **source** is server-resolved original-private object + version.
4. Canonical **validated VIDEO-relative window** is server-resolved.
5. Capability is eligible. Unmatched/empty/legacy rows stay **browsable** and
   **cannot generate**.
6. Non-public or missing resources return a **uniform unavailable** (do not
   leak existence vs unpublished vs missing).
7. Forged extra/nested source, window, or key fields are **rejected**.
   Reject arbitrary client keys and offsets.
8. Playback URLs are **generated output only**, server-derived. No full-game
   or source fallback.

### Abuse and cost (required before anonymous generation HTTP)

Hard caps must be **durable and shared** (not per-process, not browser):

- Global hard **queue capacity**
- Global **worker concurrency**
- Global **maximum new jobs per interval**
- Global **work/generation budget cutoff**

Per-client create and playback/presign quotas are **defense-in-depth** only.
They do not replace the global budget. IP/NAT limits are not a global-budget
substitute. CORS is not abuse protection.

**Fail closed** if quota storage fails **before** enqueue or signing.

Ingress: use a **canonical trusted address**. Document proxy trust. Ignore
spoofed `X-Forwarded-For`.

Canonical job uniqueness includes **play + source version + window +
encoding**. Claims must be **atomic**. Leases must be **fenced**. Server
retries/backoff are **bounded**. Request tokens, extra params, and retry
storms must not bypass caps. Bound request bodies, batches, pages, runtime,
and output size.

### Presigns and egress

Short-lived **private generated-only** presigns are intentionally shareable
bearer links. Signing caps do **not** bound download/egress. Document the
chosen delivery **bandwidth/cost ceiling**. Actual public enablement and
deployment stay **gated** until that ceiling is configured. No new paid infra
or deployment in this pass.

### Required later tests (job/playback slices, not schema)

Multi-instance concurrent duplicates; restart and timeouts; quota-storage
failure; full queue; budget cutoff; spoofed proxy; retry storms; stale
worker; revocation **before** worker start and **before** URL issuance;
sanitized errors; no source fallback. Failures must **release/recover**
bounded resources.

## Matcher persist (slice B; Later persistence implemented here)

Offline matcher → **local** DB save. No HTTP, no S3, no Neon, no routes in this
pass. `match_timestamps` calls `maybe_persist_after_match` only when
`MATCHER_PERSIST=1`. Writes require `MATCHER_PERSIST_COMMIT=1`. Dry-run is the
default. The persist entry **guards the DSN before connect** (explicit
`APP_ENV` `dev`/`test`, local host only). This slice also requires the locked
game `is_fictional is True` and a `fixtures/` key. Future live S3 must deny
fixture keys. Published/deleted predicates stay **unwired** here; later HTTP
must enforce them.

Condensed matcher `play_index` is **not** `clips.play_index`. Compound
descriptions fail closed and are not identity. There is no quarter/time/
description natural-key fallback.

### Analyzed source binding (not a copy of the current DB version)

Capture at acquisition and carry on `PersistConfig`:

- analyzed object key (`fixtures/...` in this slice; not a URL)
- immutable identity kind: `content_sha256` or `object_version_id`
- identity value (sha256 must be `sha256:` + 64 hex)

**ETag is not immutable** and is refused as an identity kind. Persist locks
the game row (`FOR UPDATE`) and compares analyzed key + identity +
`original_private` **before** attestation. Replay after a source replacement,
wrong key, or race mismatch is refused. `matched_source_version` is stamped
from the **analyzed** identity, never by copying `games.source_version`.

Prior `matcher_attested` rows for that same analyzed identity are kept when a
rerun does not rematch that play. A new persist after the game identity
changes requires a new analysis bound to the new identity.

### Play identity and bounds

Stable identity remains `UNIQUE (clips.game_id, clips.play_index)` plus
`games.bbref_id` and `players.bbref_id`. PBP rows require an **explicit**
integer `play_index` (no array-position fallback). On conflict, period +
description must match the stored row or the **entire batch is refused**
(zero mutation). Attestation SQL requires
nonnegative finite timestamp/start, ordered window, positive finite trusted
duration, `end <= duration`, `start < duration`, and `end - start <= 120`.
Invalid payload leaves a current-identity good mapping in place.

`clip_players` links only independently established per-play `participants`
(`bbref_id` + `clip_role`). There is **no** default shot role and no
config-wide player applied to unmatched/unrelated plays.

Internal persist SQL lives under `src/db/sql/queries/persist/` and is an
**explicit allowlist**, not a public serializer. Public `queries/plays`
files still must not select private columns.

Trusted `games.duration_s` is a probe of the original object at footage
ingest, not at matcher persist. Matcher persist does not set duration.

Real source media is only for later real-media validation. Fictional fixtures
are enough to develop and test persist. This persist pass does **not** claim
Security clearance. First-slice inherited blockers are merged in; QA/Security
review of both slices remains pending.

## Migration vs main vs Neon

Git `main` has no `src/db`. Neon `dev` already has a **superset** of 0001
tables (including search helpers this slice does not recreate) and no
`schema_migrations`.

The runner **never stamps 0001 merely because `public.teams` exists**.

- Empty DB (none of the required 0001 tables): apply `0001_init.sql`, verify
  required columns and uniques, stamp 0001, then apply 0003.
- Complete matching 0001 (all required tables, columns, uniques; extras OK):
  stamp 0001, then apply 0003.
- Partial or mismatched 0001: refuse. Do not stamp. Do not apply 0001 on top.
- Stamped version that does not match schema: refuse.
- Unstamped 0003: **always execute** idempotent `0003_on_demand.sql`. Preexisting
  columns, a permissive/replaced `play_capability` view, or missing named
  constraints are **not** a stamp. After apply, verify named CHECKs, the
  `nba_numeric_is_finite` function, and view definition markers, or refuse.
- Stamped 0003 whose constraints or viewdef drifted: refuse. Do not restamp.
- Apply runs in one transaction under `pg_advisory_xact_lock`. Failure rolls
  back stamps and DDL. Dirty baselines stay as they were.

0003 keeps fictional/legacy numeric rows and makes them unavailable
(`unvalidated_numeric` or `none`). It does not create playable live rows.
**Do not run against production.** This pass does not apply to Neon.

### Rollback / reapply (local/dev only)

```
# rollback 0003 (keeps 0001 tables)
psql "$LOCAL_DSN" -v ON_ERROR_STOP=1 -f src/db/sql/schema/down/0003_on_demand.down.sql

# reapply 0003
python -m db.migrate   # from src/, after DATABASE_URL points at the local DB
```

Runner requires **explicit** `APP_ENV` `dev` or `test` (empty is refused).
It refuses production/prod. Local hosts are `127.0.0.1` / `localhost` / `::1`
with no DSN query override. Neon hosts must `fullmatch` the documented
endpoint pattern, match `NEON_DEV_ENDPOINT_ID`, and use TLS `sslmode` in
`require` / `verify-ca` / `verify-full`. It never prints `DATABASE_URL`.

0003 down SQL is a single `BEGIN`/`COMMIT` transaction. **Preflight** NULL
offsets **before** dropping the view or constraints. A failed down leaves
view, constraints, stamp, and data in place.

Existing `/api/generate` and local clip routes on main are **out of scope**.
This schema does not secure them. New generate/playback routes stay unwired.

## Ordered PR dependencies (do not skip)

Identity / sign-in is **not** a hard block. Do not wait on a provider.

1. **Schema / capability** (this slice). VIEW, provenance, source identity,
   isolated tests. No HTTP persist. No runtime auth file changes. No fake users.
2. **Matcher persist.** Dedicated SQL. Wire `match_timestamps` to save
   validated VIDEO-relative offsets on the keys above. Duplicate-safe.
   Partial matches keep metadata. Local/dev fixtures only. Depends on 1.
   Independent of HTTP and of this access-policy change.
3. **Browse metadata HTTP.** Anonymous shared catalog. Allowlisted serializers
   only. Uniform unavailable for nonpublic/missing. Unmatched browsable, not
   generatable. No S3 locators. Depends on 1. Not blocked on identity.
4. **Durable shared global caps + per-client quotas.** Queue capacity, worker
   concurrency, jobs/interval, generation budget; fail closed if quota store
   fails; trusted ingress, ignore spoofed XFF. **Required before create HTTP.**
   No job/limiter code in slice 1. No new paid infra.
5. **Clip jobs / generate HTTP.** Canonical uniqueness play+source version+
   window+encoding; atomic claims; fenced leases; bounded retries. Server-
   resolved windows. Reject nested/forged keys. Depends on 2 and 4. Does not
   secure existing `/api/generate`.
6. **Playback URL.** Ready generated-only bearer presigns. No full-game
   fallback. Egress/cost ceiling documented and configured before public
   enablement. Depends on 5.
7. **Players-tab UI.** Browse depends on 3. Play depends on 6.

Requirements pin fix stays on hold (install blocker, not a pin change).
No deployment or public-bucket changes authorized.
