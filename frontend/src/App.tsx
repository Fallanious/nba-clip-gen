import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchVideos, fetchPlayByPlay, fetchTimestamps, fetchJobs } from './api';
import type { Video, PlayByPlayFile, TimestampFile, ProcessJob } from './types';
import DownloadVideo from './components/DownloadVideo';
import ScrapePlayByPlay from './components/ScrapePlayByPlay';
import MatchTimestamps from './components/MatchTimestamps';
import EditLabels from './components/EditLabels';
import GenerateClips from './components/GenerateClips';
import Processes from './components/Processes';
import UploadGame from './components/UploadGame';
import PlayerBrowser from './components/PlayerBrowser';

const steps = ['Game data', 'Match plays', 'Review', 'Generate'];

export default function App() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [pbpFiles, setPbpFiles] = useState<PlayByPlayFile[]>([]);
  const [timestampFiles, setTimestampFiles] = useState<TimestampFile[]>([]);
  const [view, setView] = useState<'players' | 'splice' | 'upload' | 'processes'>('players');
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

  useEffect(() => {
    void Promise.all([loadVideos(), loadPBP(), loadTimestamps()]).catch(() => { });
  }, [loadVideos, loadPBP, loadTimestamps]);

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

  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/">CLIP<span>FILM ROOM</span></a><nav className="side-nav" aria-label="Workspace"><button className={view === 'players' ? 'nav-selected' : 'nav-item'} onClick={() => setView('players')}>{view === 'players' ? '▸ ' : ''}Players</button><button className={view === 'splice' ? 'nav-selected' : 'nav-item'} onClick={() => { setStep(0); setView('splice'); }}>{view === 'splice' ? '▸ ' : ''}Splice</button><button className={view === 'upload' ? 'nav-selected' : 'nav-item'} onClick={() => setView('upload')}>{view === 'upload' ? '▸ ' : ''}Upload/Download</button><button className={view === 'processes' ? 'nav-selected' : 'nav-item'} onClick={() => setView('processes')}>{view === 'processes' ? '▸ ' : ''}Processes{jobs.some(job => job.status === 'running') ? ` (${jobs.filter(job => job.status === 'running').length})` : ''}</button></nav><p className="local-note">YOUR LOCAL WORKSPACE</p></aside>
    <main>
      {view === 'players' && <PlayerBrowser />}
      {view === 'splice' && <>
        <header><div><p className="eyebrow">FROM FOOTAGE TO CLIPS</p><h1>Splice</h1><p>Match the play-by-play, then cut local clips.</p></div></header>
        <nav className="steps" aria-label="Clip generation">{steps.map((s, i) => <button aria-current={step === i ? 'step' : undefined} className={step === i ? 'selected' : 'quiet'} onClick={() => setStep(i)} key={s}>{i + 1}. {s}</button>)}</nav>
        <div hidden={step !== 0}><ScrapePlayByPlay videos={videos} onComplete={loadPBP} /></div>
        <div hidden={step !== 1}><MatchTimestamps videos={videos} pbpFiles={pbpFiles} onRefreshVideos={loadVideos} onRefreshPBP={loadPBP} /></div>
        <div hidden={step !== 2}><EditLabels timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /></div>
        <div hidden={step !== 3}><GenerateClips videos={videos} timestampFiles={timestampFiles} onRefreshTimestamps={loadTimestamps} /><p className="step-note">Clips are saved for each player. Open Players to watch them.</p></div>
        <div className="wizard-footer"><button className="quiet" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>{step < 3 ? <button onClick={() => setStep(step + 1)}>Continue</button> : <button onClick={() => setView('players')}>View players</button>}</div>
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
