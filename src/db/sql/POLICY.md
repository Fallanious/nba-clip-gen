# Play capability policy (slice A)

Units and types, bound policy, provenance, and rollback.
Fictional fixtures only. No live Neon. No media access.

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
2. Order: `0 <= start < end`.
3. Zero start is valid (`start = 0` is allowed).
4. Fractional seconds are valid (`numeric`, not integer).
5. Trusted duration: `end <= games.duration_s` and `start < games.duration_s`.
   If `duration_s` is NULL, timing is unavailable. Do not guess.
6. Clip-length ceiling: `end - start <= 120` seconds.
7. Clip-length floor: `end - start >= 0.5` seconds.

Constants (must match SQL VIEW and Python):

- `CLIP_MIN_DURATION_S = 0.5`
- `CLIP_MAX_DURATION_S = 120`

## Source identity

Eligibility requires an **original private** object reference:

- `games.s3_key` non-empty, not a URL (`http(s)://` or `s3://`)
- `games.source_kind = original_private`
- `games.source_version` non-empty (ETag or VersionId of **that** object)

`public` and `derived` kinds never substitute. A public URL column is not
consulted because it is not modeled.

Fictional seeds use prefix `fixtures/` and `games.is_fictional = true`.
Those rows may be eligible in the VIEW so schema/API/UI can develop.
They **must not** be used against a real bucket. Prefix `games/` and `clips/`
are reserved for live objects and are forbidden in seed files.

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

`can_create` is **not a grant**. Browse may show ineligible rows.
Create/status/playback must revalidate and must pass fail-closed authz.
This slice does not register those routes.

## Reason precedence (first match)

1. Missing original private source ref or source version → `footage_not_matched`
2. `timing_provenance = none` (unmatched / empty timing) → `footage_not_matched`
3. `game_clock_derived` or `unvalidated_numeric` → `video_timing_unavailable`
4. Missing/invalid/out-of-range/stale video-relative window → `video_timing_unavailable`
5. Else eligible (`reason` null)

Game clock does not rescue missing timing and does not alter a valid result.

## Browse authorization

No anonymous capability API in this slice (no HTTP). Future metadata GET
must `require_user` then `authorize_player` **before** returning rows,
including ineligible ones. ETag/304 must not skip authz.

`authorize_*` does **not** invent current-user identity or ownership.
There is no owner column. Cross-user isolation is an open blocker for HTTP
slices. This slice only fails closed on missing user / empty id / empty scopes.

## Migration vs main vs Neon

Git `main` has no `src/db`. Neon `dev` already has 0001-equivalent tables
(empty games/clips) and no `schema_migrations`.

- Empty local test DB: apply `0001_init.sql` then `0003_on_demand.sql`.
- Existing Neon `dev` (not this pass): stamp 0001 if `public.teams` exists,
  then apply 0003 only. **Do not run against production.**
- 0003 keeps fictional/legacy numeric rows and makes them unavailable
  (`unvalidated_numeric` or `none`). It does not create playable rows.

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
