import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchVideos, fetchPlayByPlay, fetchTimestamps, getVideoUrl } from './api';
import type { Video, PlayByPlayFile, TimestampFile } from './types';
import DownloadVideo from './components/DownloadVideo';
import ScrapePlayByPlay from './components/ScrapePlayByPlay';
import MatchTimestamps from './components/MatchTimestamps';
import EditLabels from './components/EditLabels';
import GenerateClips from './components/GenerateClips';

type Clip = { filename: string; source_video: string; url: string; run: string; description?: string; label?: string; quarter?: number; game_time?: string; duration?: number; attributes?: { action_type?: string } };
const steps = ['Footage', 'Game data', 'Match plays', 'Review', 'Generate'];
const title = (name: string) => name.replace(/\.(mp4|mkv|webm)$/i, '').split(/[｜|]/)[0].trim();
const clock = (time?: string) => (time || '--:--').replaceAll('-', ':');

export default function App() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [pbpFiles, setPbpFiles] = useState<PlayByPlayFile[]>([]);
  const [timestampFiles, setTimestampFiles] = useState<TimestampFile[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState('');
  const [activeClip, setActiveClip] = useState<Clip | null>(null);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState('All games');
  const [run, setRun] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [wizard, setWizard] = useState(false);
  const [step, setStep] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const loadVideos = useCallback(async () => { setVideos(await fetchVideos()); }, []);
  const loadPBP = useCallback(async () => { setPbpFiles(await fetchPlayByPlay()); }, []);
  const loadTimestamps = useCallback(async () => { setTimestampFiles(await fetchTimestamps()); }, []);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/clip-library');
      if (!response.ok) throw new Error('Could not load local clips. Check that the backend is running.');
      const data = await response.json();
      setClips(data.clips);
      await Promise.all([loadVideos(), loadPBP(), loadTimestamps()]);
      setError('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load library'); }
    finally { setLoading(false); }
  }, [loadVideos, loadPBP, loadTimestamps]);


  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (wizard) dialog.current?.showModal(); else dialog.current?.close(); }, [wizard]);

  const video = videos.find(v => v.path === selected) || videos[0];
  const gameClips = clips.filter(c => c.source_video === video?.name);
  const runs = [...new Set(gameClips.map(c => c.run))];
  const shownClips = gameClips.filter(c => !run || c.run === run);
  const shownVideos = videos.filter(v => v.name.toLowerCase().includes(query.toLowerCase()) && (tab !== 'Ready to review' || clips.some(c => c.source_video === v.name)));


  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/">CLIP<span>FILM ROOM</span></a><div className="nav-selected">&#9655; <span>Library</span></div><p className="local-note">YOUR LOCAL WORKSPACE</p></aside>
    <main>
      <header><div><p className="eyebrow">SCOUT. STUDY. REPEAT.</p><h1>Game Library</h1><p>Find the plays worth a closer look.</p></div><button onClick={() => setWizard(true)}>+ Add footage</button></header>
      <div className="toolbar"><div className="tabs">{['All games', 'Ready to review'].map(t => <button className={tab === t ? 'selected' : 'quiet'} key={t} onClick={() => setTab(t)}>{t}</button>)}</div><input aria-label="Search games" placeholder="Search games, teams, dates..." value={query} onChange={e => setQuery(e.target.value)} /><button className="quiet" onClick={() => void refresh()}>Refresh</button></div>
      {error && <p role="alert" className="notice">{error}</p>}
      <div className="game-strip" aria-label="Games">{shownVideos.map(v => <button key={v.path} className={`game-choice ${video?.path === v.path ? 'current' : ''}`} onClick={() => { setSelected(v.path); setActiveClip(null); setRun(''); }}><strong>{title(v.name)}</strong><span>{v.parsed?.date || 'Local footage'} <span aria-hidden="true">/</span> {clips.filter(c => c.source_video === v.name).length} clips</span></button>)}</div>
      {!shownVideos.length && <p className="empty">{loading ? 'Loading local footage...' : 'No matching games. Add footage or place videos in the film folder.'}</p>}
      {video && <div className="workspace"><section className="player-card"><div className="card-heading"><div><h2>{title(video.name)}</h2><p>{activeClip ? (activeClip.description || 'Play') : 'Broadcast footage'} <span aria-hidden="true">/</span> {video.parsed?.date || 'Local video'}</p></div>{activeClip && <button className="quiet full-game" onClick={() => setActiveClip(null)}>Full game</button>}</div>
        <video className="main-player" key={activeClip?.url || video.path} controls preload="metadata" src={activeClip ? encodeURI(activeClip.url) : getVideoUrl(video.path)} onError={() => setError('This video could not be played. Verify the file exists and uses a browser-compatible codec.')} />
        <p className="file-note">{activeClip ? `output/clips/${activeClip.run}/${activeClip.filename}` : video.path}</p>
      </section><section className="highlights"><h2>Highlights</h2><p>{gameClips.length} clips stored locally</p>{runs.length > 1 && <select aria-label="Clip generation run" value={run} onChange={e => { setRun(e.target.value); setActiveClip(null); }}><option value="">All runs</option>{runs.map(r => <option key={r}>{r}</option>)}</select>}<div className="clip-list">{shownClips.map(c => <button key={c.url} className={`clip-row ${activeClip?.url === c.url ? 'current' : ''}`} onClick={() => setActiveClip(c)}><span><strong>{c.description || 'Play'}</strong><small>Q{c.quarter ?? '-'} · {clock(c.game_time)}{c.duration ? ` · ${Math.round(c.duration)} sec` : ''}</small></span></button>)}{!shownClips.length && <div className="empty"><h3>Your next scouting session starts here</h3><p>Add footage to find plays and generate local clips.</p><button onClick={() => setWizard(true)}>Generate clips</button></div>}</div></section></div>}
      <footer>LOCAL FOOTAGE. A CLEARER VIEW OF THE GAME.</footer>
    </main>
    <dialog ref={dialog} onCancel={() => setWizard(false)}><div className="wizard-header"><div><p className="eyebrow">ADD TO YOUR LIBRARY</p><h2>From footage to highlights</h2></div><button className="quiet" aria-label="Close setup" onClick={() => { setWizard(false); void refresh(); }}>Close</button></div><p className="wizard-intro">Download a broadcast or use footage already in film. Complete each step, then continue. Jobs keep running if you close this window.</p><nav className="steps" aria-label="Generation steps">{steps.map((s, i) => <button aria-current={step === i ? 'step' : undefined} className={step === i ? 'selected' : 'quiet'} onClick={() => setStep(i)} key={s}>{i + 1}. {s}</button>)}</nav>
      <div hidden={step !== 0}><DownloadVideo onComplete={loadVideos} /><p>Already have footage in film? Continue to game data.</p></div>
      <div hidden={step !== 1}><ScrapePlayByPlay videos={videos} onComplete={loadPBP} /></div>
      <div hidden={step !== 2}><MatchTimestamps videos={videos} pbpFiles={pbpFiles} onRefreshVideos={loadVideos} onRefreshPBP={loadPBP} onComplete={loadTimestamps} /></div>
      <div hidden={step !== 3}><EditLabels timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /></div>
      <div hidden={step !== 4}><GenerateClips videos={videos} timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /><p>Clips are saved to output/clips/&lt;timestamp&gt;. Return to the library after generation completes.</p></div>
      <div className="wizard-footer"><button className="quiet" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>{step < 4 ? <button onClick={() => setStep(step + 1)}>Continue</button> : <button onClick={() => { setWizard(false); void refresh(); }}>Return to library</button>}</div>
    </dialog>
  </div>;
}
