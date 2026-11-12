import { useEffect, useRef, useState } from 'react';
import { usePlayerClip } from '../hooks/usePlayerClip';
import { usePlayerClips } from '../hooks/usePlayerClips';
import { usePlayerLibrary } from '../hooks/usePlayerLibrary';
import type { BrowserPlayer } from '../types';

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
  const library = usePlayerLibrary();
  const clips = usePlayerClips();
  const film = usePlayerClip();
  const [broken, setBroken] = useState<Record<number, boolean>>({});
  const endRef = useRef<HTMLLIElement>(null);
  const selected = clips.selected;
  const archive = library.season ? `${library.season} archive` : '';
  const markBroken = (id: number) => setBroken(prev => ({ ...prev, [id]: true }));
  const filters = [{ role: '', label: 'All plays' }, ...clips.roles];

  useEffect(() => {
    const node = endRef.current;
    if (!node || !clips.hasMore) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) void clips.loadMore();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [clips.hasMore, clips.clips.length, clips.loadMore, selected]);

  return (
    <>
      {selected ? (
        <>
          <button className="pb-back" onClick={() => { film.clear(); clips.close(); }}>← All players</button>
          <div className="pb-card pb-selected" aria-label={`${selected.full_name} selected player`}>
            <PlayerCard
              player={selected}
              selected
              broken={!!broken[selected.id]}
              onBroken={() => markBroken(selected.id)}
            />
          </div>
          <section className="pb-plays" aria-label="Player plays">
            <div className="pb-plays-head">
              <h2>Plays</h2>
              <span role="status">{clips.loading ? 'Loading plays…' : !clips.role && selected.clips ? `${countLabel(selected.clips, 'play', 'plays')}${library.season ? ` · ${library.season}` : ''}` : ''}</span>
            </div>
            {clips.error && <p role="alert" className="notice">{clips.error}</p>}
            <div className="pb-types">
              {filters.map(item => (
                <button key={item.label} aria-pressed={clips.role === item.role} className={clips.role === item.role ? 'active' : ''} onClick={() => { film.clear(); clips.chooseRole(item.role); }}>{item.label}</button>
              ))}
            </div>
            {film.cutting && !film.url && <div className="pb-shimmer" role="status" aria-label="Cutting this play" />}
            {film.error && <p role="alert" className="notice">{film.error}</p>}
            {film.url && (
              <video className="main-player pb-film" controls preload="auto" src={film.url} />
            )}
            <ul className="pb-list">
              {clips.loading && !clips.shown.length && Array.from({ length: 5 }, (_, index) => (
                <li key={`loading-${index}`} aria-hidden="true">
                  <div className="pb-row">
                    <div>
                      <div className="pb-skeleton pb-skeleton-line" />
                      <div className="pb-skeleton pb-skeleton-line short" />
                    </div>
                    <div className="pb-skeleton pb-skeleton-line pb-kind" />
                    <div className="pb-skeleton pb-skeleton-line" />
                  </div>
                </li>
              ))}
              {clips.shown.map(clip => (
                <li key={`${clip.id}-${clip.role}`}>
                  <button
                    className={`pb-row ${film.active?.id === clip.id && film.active.role === clip.role ? 'current' : ''}`}
                    aria-pressed={film.active?.id === clip.id && film.active.role === clip.role}
                    onClick={() => film.play(selected.id, clip)}
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
              {clips.loadingMore && Array.from({ length: 2 }, (_, index) => (
                <li key={`more-${index}`} aria-hidden="true">
                  <div className="pb-row">
                    <div>
                      <div className="pb-skeleton pb-skeleton-line" />
                      <div className="pb-skeleton pb-skeleton-line short" />
                    </div>
                    <div className="pb-skeleton pb-skeleton-line pb-kind" />
                    <div className="pb-skeleton pb-skeleton-line" />
                  </div>
                </li>
              ))}
              {clips.hasMore && <li ref={endRef} className="pb-more" aria-hidden="true" />}
            </ul>
            {!clips.loading && !clips.loadingMore && !clips.shown.length && <p className="empty">No plays in this group.</p>}
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
            <input aria-label="Search players" placeholder="Search players…" value={library.query} onChange={event => library.setQuery(event.target.value)} />
            <select aria-label="Filter by team" value={library.team} onChange={event => library.setTeam(event.target.value)}>
              <option value="">All teams</option>
              {library.teams.map(code => <option key={code} value={code}>{code}</option>)}
            </select>
          </div>
          {library.error && <p role="alert" className="notice">{library.error}</p>}
          <p className="pb-count" role="status">{library.loading ? 'Loading players…' : countLabel(library.shown.length, 'player with clips', 'players with clips')}</p>
          {library.loading ? (
            <div className="pb-grid" aria-hidden="true">
              {Array.from({ length: 6 }, (_, index) => (
                <div className="pb-card pb-skeleton-card" key={index}>
                  <div className="pb-portrait pb-skeleton" />
                  <div className="pb-info">
                    <div className="pb-skeleton pb-skeleton-line" />
                    <div className="pb-skeleton pb-skeleton-line short" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="pb-grid">
              {library.shown.map(player => (
                <button className="pb-card" key={player.id} onClick={() => void clips.open(player)}>
                  <PlayerCard
                    player={player}
                    broken={!!broken[player.id]}
                    onBroken={() => markBroken(player.id)}
                  />
                </button>
              ))}
            </div>
          )}
          {!library.loading && !library.shown.length && <p className="empty">{library.players.length ? 'No players match your search.' : 'No players with clips yet.'}</p>}
        </>
      )}
    </>
  );
}
