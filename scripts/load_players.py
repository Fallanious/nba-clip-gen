"""Load 2026-27 NBA rosters from Basketball Reference into `players`.

Requirements: requests, beautifulsoup4, lxml, psycopg[binary]
Usage: DATABASE_URL=... python load_players.py        (scrape + headshots + upsert, one run)
       python load_players.py --sql-out players_upsert.sql   (same, writes one upsert statement
           + players_upsert.sql.sources.csv for running via the Neon connector)
       python load_players.py --headshots-sql-out list.tsv out.sql   (headshots only, existing rows)
headshot_url: BBRef /req/<ver>/images/headshots/<bbref_id>.jpg verified by HEAD (200 + image/*);
fallback nba_api exact accent-insensitive name+team match -> cdn.nba.com; else NULL.
Idempotent: upserts on bbref_id. Single transaction.
Traded players on multiple rosters: deterministic -- the team whose abbr sorts
LAST alphabetically wins (all conflicts are printed).
"""
import os, re, sys, time, unicodedata
import requests
from bs4 import BeautifulSoup, Comment

# Add src to path for config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SEASON = 2027
URL = "https://www.basketball-reference.com/teams/{abbr}/{season}.html"
DELAY = 4.0
BBREF_TEAMS = "ATL BOS BRK CHI CHO CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHO POR SAC SAS TOR UTA WAS".split()
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
UA = {"User-Agent": "nba-clip-gen roster loader (personal project)"}

def fetch(abbr):
    for attempt in range(5):
        r = requests.get(URL.format(abbr=abbr, season=SEASON), headers=UA, timeout=30)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60 * (attempt + 1)))
            print(f"429 on {abbr}, sleeping {wait}s", file=sys.stderr); time.sleep(wait); continue
        if r.status_code == 403 or "captcha" in r.text.lower()[:5000]:
            sys.exit(f"Blocked by BBRef on {abbr}: HTTP {r.status_code}")
        r.raise_for_status(); r.encoding = "utf-8"; return r.text
    sys.exit(f"Gave up on {abbr} after repeated 429s")

def split_name(full):
    toks = full.split()
    core = toks[:]
    while len(core) > 2 and core[-1].lower().strip(",") in SUFFIXES:
        core.pop()
    if len(core) == 1: return None, core[0]
    return core[0], " ".join(core[1:])

def parse(html):
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="roster")
    if table is None:
        for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
            if 'id="roster"' in c:
                table = BeautifulSoup(c, "lxml").find("table", id="roster"); break
    if table is None: return []
    out = []
    for tr in table.tbody.find_all("tr"):
        td = tr.find("td", {"data-stat": "player"}); a = td and td.find("a")
        if not a: continue
        m = re.search(r"/players/[a-z]/([a-z0-9]+)\.html", a["href"])
        if not m: continue
        pos = tr.find("td", {"data-stat": "pos"})
        out.append((m.group(1), a.get_text(strip=True), pos.get_text(strip=True) if pos else None))
    return out

UPSERT_SQL = """
insert into players (bbref_id, full_name, first_name, last_name, team_id, position, headshot_url, is_active)
select v.bbref_id, v.full_name, v.first_name, v.last_name, t.id, v.position, v.headshot_url, true
from (values {values}) as v(bbref_id, full_name, first_name, last_name, abbr, position, headshot_url)
join teams t on t.abbr = v.abbr
on conflict (bbref_id) do update set full_name=excluded.full_name,
  first_name=excluded.first_name, last_name=excluded.last_name,
  team_id=excluded.team_id, position=excluded.position,
  headshot_url=excluded.headshot_url,
  is_active=true, updated_at=now()"""

def lit(x):
    return "NULL" if x is None else "'" + str(x).replace("'", "''") + "'"

def scrape(abbrs):
    players = {}
    for i, abbr in enumerate(sorted(abbrs)):
        if i: time.sleep(DELAY)
        rows = parse(fetch(abbr))
        print(f"{abbr}: {len(rows)} players", file=sys.stderr)
        for bid, name, pos in rows:
            if bid in players:
                print(f"multi-roster: {bid} {name} {players[bid][3]} -> {abbr}", file=sys.stderr)
            players[bid] = (bid, name, pos, abbr)
    return players

def build_sql(players, shots=None):
    shots = shots or {}
    vals = []
    for bid, name, pos, abbr in players.values():
        first, last = split_name(name)
        url = shots.get(bid, (None, None))[0]
        vals.append("(" + ",".join(lit(x) for x in (bid, name, first, last, abbr, pos or None, url)) + ")")
    return UPSERT_SQL.format(values=",\n".join(vals))


# ---- headshots -------------------------------------------------------------
HEADSHOT_VER = "202610140"
BBREF_HEADSHOT = "https://www.basketball-reference.com/req/{ver}/images/headshots/{bid}.jpg"
NBA_HEADSHOT = "https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png"
NBA_TO_BBREF = {"CHA": "CHO", "PHX": "PHO", "BKN": "BRK"}

def check_image(url):
    for attempt in range(5):
        r = requests.head(url, headers=UA, timeout=30, allow_redirects=True)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60 * (attempt + 1)))
            print(f"429 on headshot check, sleeping {wait}s", file=sys.stderr)
            time.sleep(wait); continue
        if r.status_code == 403:
            sys.exit(f"403 from {url}; stopping")
        return r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
    sys.exit("Gave up after repeated 429s")

def norm(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.sub(r"[^a-z ]", "", n).split() if t not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return " ".join(toks)

def nba_fallback(missing):
    """missing: list of (bid, full_name, abbr). Returns {bid: url}; only exact
    accent-insensitive name + team matches, unique, are accepted."""
    try:
        from nba_api.stats.endpoints import commonallplayers
        df = commonallplayers.CommonAllPlayers(is_only_current_season=1, season="2026-27").get_data_frames()[0]
        idx = {}
        for _, r in df.iterrows():
            key = (norm(r["DISPLAY_FIRST_LAST"]), NBA_TO_BBREF.get(r["TEAM_ABBREVIATION"], r["TEAM_ABBREVIATION"]))
            idx.setdefault(key, []).append(int(r["PERSON_ID"]))
        out = {}
        for bid, name, abbr in missing:
            ids = idx.get((norm(name), abbr), [])
            if len(ids) == 1:
                out[bid] = NBA_HEADSHOT.format(pid=ids[0])
        return out
    except ImportError:
        print("Warning: nba_api not installed, skipping NBA.com fallback", file=sys.stderr)
        return {}

CACHE = os.environ.get("HEADSHOT_CACHE", "headshot_check_cache.tsv")

def headshots(players, delay=DELAY):
    """players: list of (bid, full_name, abbr). Returns {bid: (url, source)}.
    BBRef check results are cached in CACHE so an interrupted run resumes."""
    cache = {}
    if os.path.exists(CACHE):
        cache = dict(l.rstrip("\n").split("\t") for l in open(CACHE) if l.strip())
    # Sample gate: HEAD ~10 derived URLs first. If none work, the prefix/build
    # is stale -> everything goes to the nba_api fallback.
    sample = [p for p in players if p[0] not in cache][:10]
    with open(CACHE, "a") as cf:
        for bid, _, _ in sample:
            time.sleep(delay)
            cache[bid] = "1" if check_image(BBREF_HEADSHOT.format(ver=HEADSHOT_VER, bid=bid)) else "0"
            cf.write(f"{bid}\t{cache[bid]}\n"); cf.flush()
    if sample and not any(cache[b] == "1" for b, _, _ in sample):
        print("BBRef headshot prefix failed sample; using nba_api only", file=sys.stderr)
        fb = nba_fallback(players)
        return {b: (u, "nba_cdn") for b, u in fb.items()}
    res, missing = {}, []
    with open(CACHE, "a") as cf:
        for i, (bid, name, abbr) in enumerate(players):
            url = BBREF_HEADSHOT.format(ver=HEADSHOT_VER, bid=bid)
            if bid not in cache:
                time.sleep(delay)
                cache[bid] = "1" if check_image(url) else "0"
                cf.write(f"{bid}\t{cache[bid]}\n"); cf.flush()
            if cache[bid] == "1": res[bid] = (url, "bbref")
            else: missing.append((bid, name, abbr))
            if i % 50 == 0: print(f"checked {i}/{len(players)}", file=sys.stderr)
    if missing:
        for bid, url in nba_fallback(missing).items():
            res[bid] = (url, "nba_cdn")
    return res

def write_sources(path, bids, res):
    """Record where each headshot_url came from: bbref | nba_cdn | none."""
    with open(path, "w") as f:
        f.write("bbref_id,source,url\n")
        for b in bids:
            u, src = res.get(b, ("", "none"))
            f.write(f"{b},{src},{u}\n")

def headshot_sql(res):
    vals = ",\n".join(f"({lit(b)},{lit(u)})" for b, (u, _) in res.items())
    return ("update players p set headshot_url = v.url, updated_at = now()\n"
            f"from (values {vals}) as v(bbref_id, url) where p.bbref_id = v.bbref_id")

def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--sql-out":
        t0 = time.time()
        players = scrape(BBREF_TEAMS)
        shots = headshots([(b, n, a) for b, n, _, a in players.values()])
        open(sys.argv[2], "w").write(build_sql(players, shots))
        write_sources(sys.argv[2] + ".sources.csv", players.keys(), shots)
        print(f"elapsed {time.time()-t0:.0f}s", file=sys.stderr)
        print(f"wrote upsert for {len(players)} players", file=sys.stderr); return
    if len(sys.argv) == 4 and sys.argv[1] == "--headshots-sql-out":
        rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[2]) if l.strip()]
        res = headshots(rows)
        open(sys.argv[3], "w").write(headshot_sql(res))
        write_sources(sys.argv[3] + ".sources.csv", [r[0] for r in rows], res)
        print(f"headshots: {len(res)}/{len(rows)}", file=sys.stderr); return

    # Use DATABASE_URL from environment or config
    try:
        import psycopg
        from config import config
        db_url = config.DATABASE_URL
    except ImportError:
        print("Error: psycopg not installed. Install with: pip install 'psycopg[binary]'", file=sys.stderr)
        sys.exit(1)

    if not db_url:
        print("Error: DATABASE_URL not set in environment", file=sys.stderr)
        sys.exit(1)

    with psycopg.connect(db_url) as conn:
        teams = dict(conn.execute("select abbr, id from teams").fetchall())
        players = scrape(teams)
        shots = headshots([(b, n, a) for b, n, _, a in players.values()])
        with conn.transaction():
            conn.execute(build_sql(players, shots))
        print(f"upserted {len(players)} players", file=sys.stderr)

if __name__ == "__main__":
    main()
