import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchVideos, fetchPlayByPlay, fetchTimestamps, fetchJobs, getVideoUrl } from './api';
import type { Video, PlayByPlayFile, TimestampFile, ProcessJob } from './types';
import DownloadVideo from './components/DownloadVideo';
import ScrapePlayByPlay from './components/ScrapePlayByPlay';
import MatchTimestamps from './components/MatchTimestamps';
import EditLabels from './components/EditLabels';
import GenerateClips from './components/GenerateClips';
import Processes from './components/Processes';
import UploadGame from './components/UploadGame';
import PlayerBrowser from './components/PlayerBrowser';

type Clip = { filename: string; source_video: string; url: string; run: string; description?: string; label?: string; quarter?: number; game_time?: string; duration?: number; attributes?: { action_type?: string } };
const steps = ['Game data', 'Match plays', 'Review', 'Generate'];
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
  const [view, setView] = useState<'library' | 'players' | 'splice' | 'upload' | 'processes'>('library');
  const [step, setStep] = useState(0);
  const [jobs, setJobs] = useState<ProcessJob[]>([]);
  const seenJobs = useRef<Record<string, string>>({});
  const loadVideos = useCallback(async () => { setVideos(await fetchVideos()); }, []);
  const loadPBP = useCallback(async () => { setPbpFiles(await fetchPlayByPlay()); }, []);
  const loadTimestamps = useCallback(async () => { setTimestampFiles(await fetchTimestamps()); }, []);
  const refreshJobs = useCallback(async () => {
    const list = await fetchJobs();
    setJobs(list);
    let finishedMatch = false;
    let finishedDownload = false;
    for (const job of list) {
      const prev = seenJobs.current[job.id];
      seenJobs.current[job.id] = job.status;
      const justFinished = prev === 'running' && job.status === 'completed';
      if (justFinished && job.kind === 'Match timestamps') finishedMatch = true;
      if (justFinished && job.kind === 'Download') finishedDownload = true;
    }
    if (finishedMatch) await loadTimestamps();
    if (finishedDownload) await loadVideos();
  }, [loadTimestamps, loadVideos]);

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

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        if (!stop) await refreshJobs();
      } catch { /* backend may be restarting */ }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 3000);
    return () => { stop = true; window.clearInterval(id); };
  }, [refreshJobs]);

  const video = videos.find(v => v.path === selected) || videos[0];
  const gameClips = clips.filter(c => c.source_video === video?.name);
  const runs = [...new Set(gameClips.map(c => c.run))];
  const shownClips = gameClips.filter(c => !run || c.run === run);
  const shownVideos = videos.filter(v => v.name.toLowerCase().includes(query.toLowerCase()) && (tab !== 'Ready to review' || clips.some(c => c.source_video === v.name)));


  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/">CLIP<span>FILM ROOM</span></a><nav className="side-nav" aria-label="Workspace"><button className={view === 'library' ? 'nav-selected' : 'nav-item'} onClick={() => setView('library')}>{view === 'library' ? '▸ ' : ''}Library</button><button className={view === 'players' ? 'nav-selected' : 'nav-item'} onClick={() => setView('players')}>{view === 'players' ? '▸ ' : ''}Players</button><button className={view === 'splice' ? 'nav-selected' : 'nav-item'} onClick={() => { setStep(0); setView('splice'); }}>{view === 'splice' ? '▸ ' : ''}Splice</button><button className={view === 'upload' ? 'nav-selected' : 'nav-item'} onClick={() => setView('upload')}>{view === 'upload' ? '▸ ' : ''}Upload/Download</button><button className={view === 'processes' ? 'nav-selected' : 'nav-item'} onClick={() => setView('processes')}>{view === 'processes' ? '▸ ' : ''}Processes{jobs.some(job => job.status === 'running') ? ` (${jobs.filter(job => job.status === 'running').length})` : ''}</button></nav><p className="local-note">YOUR LOCAL WORKSPACE</p></aside>
    <main>
      {view === 'library' && <>
        <header><div><p className="eyebrow">SCOUT. STUDY. REPEAT.</p><h1>Game Library</h1><p>Find the plays worth a closer look.</p></div></header>
        <div className="toolbar"><div className="tabs">{['All games', 'Ready to review'].map(t => <button className={tab === t ? 'selected' : 'quiet'} key={t} onClick={() => setTab(t)}>{t}</button>)}</div><input aria-label="Search games" placeholder="Search games, teams, dates..." value={query} onChange={e => setQuery(e.target.value)} /><button className="quiet" onClick={() => void refresh()}>Refresh</button></div>
        {error && <p role="alert" className="notice">{error}</p>}
        <div className="game-strip" aria-label="Games">{shownVideos.map(v => <button key={v.path} className={`game-choice ${video?.path === v.path ? 'current' : ''}`} onClick={() => { setSelected(v.path); setActiveClip(null); setRun(''); }}><strong>{title(v.name)}</strong><span>{v.parsed?.date || 'Local footage'} <span aria-hidden="true">/</span> {clips.filter(c => c.source_video === v.name).length} clips</span></button>)}</div>
        {!shownVideos.length && <p className="empty">{loading ? 'Loading local footage...' : 'No matching games. Add footage or place videos in the film folder.'}</p>}
        {video && <div className="workspace"><section className="player-card"><div className="card-heading"><div><h2>{title(video.name)}</h2><p>{activeClip ? (activeClip.description || 'Play') : 'Broadcast footage'} <span aria-hidden="true">/</span> {video.parsed?.date || 'Local video'}</p></div>{activeClip && <button className="quiet full-game" onClick={() => setActiveClip(null)}>Full game</button>}</div>
          <video className="main-player" key={activeClip?.url || video.path} controls preload="metadata" src={activeClip ? encodeURI(activeClip.url) : getVideoUrl(video.path)} onError={() => setError('This video could not be played. Verify the file exists and uses a browser-compatible codec.')} />
          <p className="file-note">{activeClip ? `output/clips/${activeClip.run}/${activeClip.filename}` : video.path}</p>
        </section><section className="highlights"><h2>Highlights</h2><p>{gameClips.length} clips stored locally</p>{runs.length > 1 && <select aria-label="Clip generation run" value={run} onChange={e => { setRun(e.target.value); setActiveClip(null); }}><option value="">All runs</option>{runs.map(r => <option key={r}>{r}</option>)}</select>}<div className="clip-list">{shownClips.map(c => <button key={c.url} className={`clip-row ${activeClip?.url === c.url ? 'current' : ''}`} onClick={() => setActiveClip(c)}><span><strong>{c.description || 'Play'}</strong><small>Q{c.quarter ?? '-'} · {clock(c.game_time)}{c.duration ? ` · ${Math.round(c.duration)} sec` : ''}</small></span></button>)}{!shownClips.length && <div className="empty"><h3>Your next scouting session starts here</h3><p>Add footage to find plays and generate local clips.</p><button onClick={() => { setStep(3); setView('splice'); }}>Generate clips</button></div>}</div></section></div>}
        <footer>LOCAL FOOTAGE. A CLEARER VIEW OF THE GAME.</footer>
      </>}
      {view === 'players' && <PlayerBrowser />}
      {view === 'splice' && <>
        <header><div><p className="eyebrow">FROM FOOTAGE TO CLIPS</p><h1>Splice</h1><p>Match the play-by-play, then cut local clips.</p></div></header>
        <nav className="steps" aria-label="Clip generation">{steps.map((s, i) => <button aria-current={step === i ? 'step' : undefined} className={step === i ? 'selected' : 'quiet'} onClick={() => setStep(i)} key={s}>{i + 1}. {s}</button>)}</nav>
        <div hidden={step !== 0}><ScrapePlayByPlay videos={videos} onComplete={loadPBP} /></div>
        <div hidden={step !== 1}><MatchTimestamps videos={videos} pbpFiles={pbpFiles} onRefreshVideos={loadVideos} onRefreshPBP={loadPBP} /></div>
        <div hidden={step !== 2}><EditLabels timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /></div>
        <div hidden={step !== 3}><GenerateClips videos={videos} timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /><p className="step-note">Clips are saved to output/clips/&lt;timestamp&gt;. Return to the library after generation completes.</p></div>
        <div className="wizard-footer"><button className="quiet" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>{step < 3 ? <button onClick={() => setStep(step + 1)}>Continue</button> : <button onClick={() => { setView('library'); void refresh(); }}>Return to library</button>}</div>
      </>}
      {view === 'upload' && <>
        <header><div><p className="eyebrow">STORE THE GAME</p><h1>Upload/Download</h1><p>Download a broadcast into the film folder, or send a local file to the private bucket. A 720p MKV goes up as-is. Anything larger is converted first.</p></div></header>
        <DownloadVideo onStarted={() => void refreshJobs()} />
        <UploadGame videos={videos} onStarted={() => void refreshJobs()} />
      </>}
      {view === 'processes' && <Processes jobs={jobs} onChanged={() => void refreshJobs()} />}
    </main>
  </div>;
}
