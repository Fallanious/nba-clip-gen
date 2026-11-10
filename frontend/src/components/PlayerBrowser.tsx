import { useEffect, useRef, useState } from 'react';
import { fetchPlayerClip, fetchPlayerClips, fetchPlayerLibrary } from '../api';
import type { BrowserPlayer, PlayerClip } from '../types';

const TEAM_COLOR: Record<string, string> = {
  ATL: '#E03A3E', BOS: '#007A33', BRK: '#111111', CHI: '#CE1141', CHO: '#1D1160',
  CLE: '#860038', DAL: '#00538C', DEN: '#0E2240', DET: '#C8102E', GSW: '#1D428A',
  HOU: '#CE1141', IND: '#002D62', LAC: '#C8102E', LAL: '#552583', MEM: '#5D76A9',
  MIA: '#98002E', MIL: '#00471B', MIN: '#0C2340', NOP: '#0C2340', NYK: '#006BB6',
  OKC: '#007AC1', ORL: '#0077C0', PHI: '#006BB6', PHO: '#1D1160', POR: '#E03A3E',
  SAC: '#5A2D81', SAS: '#1D1D1D', TOR: '#CE1141', UTA: '#002B5C', WAS: '#002B5C',
};

function countLabel(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function PlayerCard({ player, selected, broken, onBroken }: {
  player: BrowserPlayer;
  selected?: boolean;
  broken: boolean;
  onBroken: () => void;
}) {
  return (
    <>
      <div className="pb-portrait">
        {player.team && <span className="pb-badge" style={{ background: TEAM_COLOR[player.team] || '#242426' }}>{player.team}</span>}
        {player.headshot_url && !broken && (
          <img src={player.headshot_url} alt="" referrerPolicy="no-referrer" onError={onBroken} />
        )}
      </div>
      <div className="pb-info">
        <strong>{player.full_name}</strong>
        <div className="pb-meta">{[player.position, countLabel(player.games, 'game', 'games')].filter(Boolean).join(' · ')}</div>
        <div className="pb-bottom">
          <span>{countLabel(player.clips, 'clip', 'clips')}</span>
          <span>{selected ? 'Selected player' : 'View film ↗'}</span>
        </div>
      </div>
    </>
  );
}

export default function PlayerBrowser() {
  const [players, setPlayers] = useState<BrowserPlayer[]>([]);
  const [season, setSeason] = useState('');
  const [query, setQuery] = useState('');
  const [team, setTeam] = useState('');
  const [selected, setSelected] = useState<BrowserPlayer | null>(null);
  const [clips, setClips] = useState<PlayerClip[]>([]);
  const [role, setRole] = useState('All plays');
  const [active, setActive] = useState<PlayerClip | null>(null);
  const [broken, setBroken] = useState<Record<number, boolean>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [clipsLoading, setClipsLoading] = useState(false);
  const [cutting, setCutting] = useState(false);
  const [cutError, setCutError] = useState('');
  const [clipUrl, setClipUrl] = useState('');
  const clipUrlRef = useRef('');
  const requestId = useRef(0);

  function clearClip() {
    if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
    clipUrlRef.current = '';
    setClipUrl('');
  }

  useEffect(() => () => {
    if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
  }, []);

  useEffect(() => {
    let stop = false;
    fetchPlayerLibrary()
      .then(data => {
        if (stop) return;
        setPlayers(data.players);
        setSeason(data.season);
        setError('');
      })
      .catch(err => {
        if (!stop) setError(err instanceof Error ? err.message : 'Could not load players.');
      })
      .finally(() => { if (!stop) setLoading(false); });
    return () => { stop = true; };
  }, []);

  async function openPlayer(player: BrowserPlayer) {
    setSelected(player);
    setRole('All plays');
    setActive(null);
    setClips([]);
    setError('');
    setClipsLoading(true);
    try {
      const data = await fetchPlayerClips(player.id);
      setClips(data.clips);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load plays.');
    } finally {
      setClipsLoading(false);
    }
  }

  const teams = [...new Set(players.map(player => player.team).filter(Boolean))].sort();
  const shown = players.filter(player => {
    const name = player.full_name.toLowerCase().includes(query.trim().toLowerCase());
    return name && (!team || player.team === team);
  });
  const roles = ['All plays', ...new Set(clips.map(clip => clip.role_label))];
  const shownClips = clips.filter(clip => role === 'All plays' || clip.role_label === role);
  const archive = season ? `${season} archive` : '';

  return (
    <>
      {selected ? (
        <>
          <button className="pb-back" onClick={() => { requestId.current += 1; setSelected(null); setActive(null); setError(''); setCutError(''); setCutting(false); clearClip(); }}>← All players</button>
          <div className="pb-card pb-selected" aria-label={`${selected.full_name} selected player`}>
            <PlayerCard
              player={selected}
              selected
              broken={!!broken[selected.id]}
              onBroken={() => setBroken(prev => ({ ...prev, [selected.id]: true }))}
            />
          </div>
          <section className="pb-plays" aria-label="Player plays">
            <div className="pb-plays-head">
              <h2>Plays</h2>
              <span>{clips.length ? `${countLabel(clips.length, 'play', 'plays')}${selected && season ? ` · ${season}` : ''}` : ''}</span>
            </div>
            {error && <p role="alert" className="notice">{error}</p>}
            <div className="pb-types">
              {roles.map(label => (
                <button key={label} aria-pressed={role === label} className={role === label ? 'active' : ''} onClick={() => { requestId.current += 1; setRole(label); setActive(null); setCutting(false); setCutError(''); clearClip(); }}>{label}</button>
              ))}
            </div>
            {cutting && !clipUrl && <div className="pb-shimmer" role="status" aria-label="Cutting this play" />}
            {cutError && <p role="alert" className="notice">{cutError}</p>}
            {clipUrl && (
              <video className="main-player pb-film" controls preload="none" src={clipUrl} />
            )}
            <ul className="pb-list">
              {shownClips.map(clip => (
                <li key={`${clip.id}-${clip.role}`}>
                  <button
                    className={`pb-row ${active?.id === clip.id && active.role === clip.role ? 'current' : ''}`}
                    aria-pressed={active?.id === clip.id && active.role === clip.role}
                    onClick={() => {
                      if (active?.id === clip.id && clipUrl) return;
                      const token = ++requestId.current;
                      setCutError('');
                      setCutting(true);
                      setActive(clip);
                      clearClip();
                      void fetchPlayerClip(selected.id, clip.id)
                        .then(blob => {
                          if (requestId.current !== token) return;
                          const url = URL.createObjectURL(blob);
                          clipUrlRef.current = url;
                          setClipUrl(url);
                        })
                        .catch(err => {
                          if (requestId.current !== token) return;
                          setCutError(err instanceof Error ? err.message : 'Could not cut this clip.');
                        })
                        .finally(() => {
                          if (requestId.current === token) setCutting(false);
                        });
                    }}
                  >
                    <span>
                      <strong>{clip.description}</strong>
                      <small>{clip.matchup} · Q{clip.period} · {clip.game_clock}</small>
                    </span>
                    <span className="pb-kind">{clip.role_label}</span>
                    <span className="pb-duration">{clip.duration_label}</span>
                  </button>
                </li>
              ))}
            </ul>
            {!clipsLoading && !shownClips.length && <p className="empty">No plays in this group.</p>}
          </section>
        </>
      ) : (
        <>
          <header className="pb-head">
            <div>
              <p className="eyebrow">SCOUT. STUDY. REPEAT.</p>
              <div className="pb-title">
                <h1>Player Library</h1>
                {archive && <span>{archive}</span>}
              </div>
              <p>Find a player. Get straight to the film.</p>
            </div>
          </header>
          <div className="toolbar pb-toolbar">
            <input aria-label="Search players" placeholder="Search players…" value={query} onChange={event => setQuery(event.target.value)} />
            <select aria-label="Filter by team" value={team} onChange={event => setTeam(event.target.value)}>
              <option value="">All teams</option>
              {teams.map(code => <option key={code} value={code}>{code}</option>)}
            </select>
          </div>
          {error && <p role="alert" className="notice">{error}</p>}
          <p className="pb-count">{loading ? 'Loading players…' : countLabel(shown.length, 'player with clips', 'players with clips')}</p>
          <div className="pb-grid">
            {shown.map(player => (
              <button className="pb-card" key={player.id} onClick={() => void openPlayer(player)}>
                <PlayerCard
                  player={player}
                  broken={!!broken[player.id]}
                  onBroken={() => setBroken(prev => ({ ...prev, [player.id]: true }))}
                />
              </button>
            ))}
          </div>
          {!loading && !shown.length && <p className="empty">{players.length ? 'No players match your search.' : 'No players with clips yet.'}</p>}
        </>
      )}
    </>
  );
}
