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
   SQL uses `x = x` (rejects NaN) and `x <> 'Infinity'` / `x <> '-Infinity'`.
   There is no `1e9` storage cap.
2. Order: `0 <= start < end`. Tiny positive windows are valid. There is **no**
   minimum clip length.
3. Zero start is valid (`start = 0` is allowed).
4. Fractional seconds are valid (`numeric`, not integer).
5. Trusted duration: `end <= games.duration_s` and `start < games.duration_s`.
   If `duration_s` is NULL, timing is unavailable. Do not guess.
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

Fictional seeds use prefix `fixtures/` and `games.is_fictional = true`.
`can_create` may be true so schema/UI can exercise eligibility. That is **not**
live media access.

`live_media_access_permitted` is independent of `can_create` and is false when:

- `is_fictional` is true, or
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

This is **not** stub identity and **not** no-op auth pretending to be a user.
Do not create fake authenticated users, `LocalUser`, scopes, or owner IDs.
Do not add `owner_id` / `user_id` / org ACL columns. Do not implement a
per-user data model. Do not register `before_request_hook` to invent `g.user`.

Still mandatory (shared catalog is not a bypass):

- Capability / eligibility (`can_create`, reasons). Ineligible rows stay
  browsable and are not creatable or playable.
- Published / deleted / resource visibility when those states exist. Do not
  resurrect deleted rows or skip unpublished hiding.
- Serializers must not widen private metadata. Browse stays on the
  allowlist in `metadata_allowlist.py` (`play_id`, game/play display fields,
  `can_create`, `reason`). No keys, versions, offsets, URLs, or matcher ids.
- S3 **source and output** stay private. Server resolves play/game IDs,
  original source, and video windows. Reject arbitrary client keys and
  client offsets.
- Playback is **generated-clip-only** when the clip is ready. No full-game
  source fallback.
- Anonymous create is costly. Before any create HTTP: bounded **global and
  per-client** rate limits, concurrency limits, queue and retry controls,
  and durable duplicate safety. Those are not this slice.
- Security is reviewing this access model. No deployment, public-bucket, or
  new paid infra in this pass.

This slice does **not** change `src/auth.py` or `src/authz.py`. Existing
helpers stay unused. Existing `/api/generate` is still out of scope.

Offline matcher persistence is independent of this access policy and of HTTP.

## Later persistence (not this slice)

Matcher → Neon save is a **following** slice. This schema is the contract it
must reuse. Do not add persist SQL, `match_timestamps` DB writes, or clip_jobs
here.

Stable identity (already in 0001 + 0003):

| Thing | Key | Notes |
| --- | --- | --- |
| Game | `games.bbref_id` UNIQUE | Basketball-Reference game id |
| Play | `UNIQUE (clips.game_id, clips.play_index)` | Stored PBP index, not matcher array order |
| Player | `players.bbref_id` UNIQUE + `clip_players` | Role on that play |
| Footage | `games.s3_key` + `source_kind = original_private` + `games.source_version` | Original object only |

Persist must:

1. Look up the game by `bbref_id`, the play by `(game_id, play_index)`, the
   player by `bbref_id`. Never invent ids.
2. Upsert on `(game_id, play_index)` (duplicate-safe reruns).
3. Write offsets only when provenance can be `matcher_attested` **and** the
   source identity/version matches **and** VIDEO-relative bounds pass.
4. Partial / unmatched / invalid matcher rows keep PBP metadata. Offsets stay
   NULL. Provenance stays `none`. Never guess from game clock or neighbors.
5. `public` / `derived` refs never substitute. Do not persist a public URL as
   the original.
6. Use dedicated files under `src/db/sql/queries/` (later slice). Isolated
   local/dev fixtures only. No production import.
7. Call `live_media_access_permitted` before any object access.

`match_play_timestamps.py` today writes JSON and uses condensed-list
`play_index`. Persist must map to the **stored** PBP `play_index`, not that
array position. Do not rewrite the matcher model in the persist slice.

Trusted `games.duration_s` is a probe of the original object at footage
ingest, not at matcher persist. Matcher persist does not set duration.

Real source media is only for later real-media validation. Fictional fixtures
are enough to develop and test persist.

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
- Partial 0003 objects: refuse.
- Apply runs in one transaction. Failure rolls back stamps and DDL.

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

Runner refuses `APP_ENV` production/prod and Neon hosts that are not the
guarded local/test or documented dev endpoint. It never prints `DATABASE_URL`.

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
   only. Capability + published/deleted visibility. No S3 locators. Server-
   resolved IDs. Depends on 1. Not blocked on identity.
4. **Abuse controls for anonymous create.** Bounded global and per-client
   rate, concurrency, queue, retry, durable duplicate safety. **Required
   before create HTTP.** No new paid infra in this pass.
5. **Clip jobs / generate HTTP.** Server-resolves play + source version +
   offsets. Rejects client keys/offsets. Depends on 2 and 4. Does not secure
   existing `/api/generate`.
6. **Playback URL.** Ready generated clip only. No full-game fallback. No
   source/output key leak. Depends on 5.
7. **Players-tab UI.** Browse depends on 3. Play depends on 6.

Requirements pin fix stays on hold (install blocker, not a pin change).
No deployment or public-bucket changes authorized.
