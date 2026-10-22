import { useEffect, useRef, useState } from 'react';
import { fetchPlayByPlay, fetchBBRefUrl, startScrape } from '../api';
import { useJob } from '../hooks/useJob';
import type { Video } from '../types';
import Section from './Section';

interface Props {
  videos: Video[];
  onComplete: () => void;
}

const gameIdFromUrl = (url: string) => (url.trim().split('/').pop() || '').replace(/\.html.*$/, '');

export default function ScrapePlayByPlay({ videos, onComplete }: Props) {
  const [selectedVideo, setSelectedVideo] = useState('');
  const [url, setUrl] = useState('');
  const [lookupNote, setLookupNote] = useState('');
  const [outcome, setOutcome] = useState<'idle' | 'running' | 'success' | 'failure'>('idle');
  const [message, setMessage] = useState('');
  const job = useJob();
  const started = useRef(0);
  const checking = useRef(false);
  const targetId = useRef('');

  useEffect(() => {
    if (job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') return;
    if (checking.current) return;
    checking.current = true;

    const confirmSaved = async () => {
      try {
        const files = await fetchPlayByPlay();
        const saved = files
          .filter(file => {
            const fresh = file.mtime * 1000 >= started.current - 2000 && file.size > 2;
            const named = !targetId.current || file.name === `${targetId.current}.json`;
            return fresh && named;
          })
          .sort((a, b) => b.mtime - a.mtime)[0];
        if (saved) {
          setOutcome('success');
          setMessage(`Saved to playbyplay/${saved.name}`);
          onComplete();
          return;
        }
      } catch {
        setOutcome('failure');
        setMessage('Scrape failed. Could not check the play-by-play folder.');
        return;
      }
      setOutcome('failure');
      setMessage('Scrape failed. Nothing new was saved to playbyplay.');
    };

    void confirmSaved();
  }, [job.status, onComplete]);

  const handleFindUrl = async () => {
    if (!selectedVideo) return;
    setLookupNote('');
    try {
      const data = await fetchBBRefUrl(selectedVideo);
      if (data.bbref_url) {
        setUrl(data.bbref_url);
        return;
      }
      setLookupNote("Couldn't match this title to a Basketball Reference game.");
    } catch {
      setLookupNote("Couldn't match this title to a Basketball Reference game.");
    }
  };

  const handleScrape = async () => {
    if (!url.trim() || outcome === 'running') return;
    checking.current = false;
    started.current = Date.now();
    targetId.current = gameIdFromUrl(url);
    setOutcome('running');
    setMessage('Scraping play-by-play...');
    try {
      const data = await startScrape(url.trim());
      if (data.error) {
        setOutcome('failure');
        setMessage(data.error);
        return;
      }
      job.startPolling(data.job_id);
    } catch (err) {
      setOutcome('failure');
      setMessage(err instanceof Error ? err.message : 'Scrape failed.');
    }
  };

  return (
    <Section title="2. Scrape Play-by-Play">
      <div className="form-group">
        <label>Game</label>
        <select value={selectedVideo} onChange={e => { setSelectedVideo(e.target.value); setLookupNote(''); }}>
          <option value="">-- Select Video --</option>
          {videos.map(v => (
            <option key={v.path} value={v.path}>
              {v.name} ({v.size_mb} MB)
            </option>
          ))}
        </select>
      </div>
      <div className="form-group">
        <label>Basketball Reference URL</label>
        <div className="form-row">
          <input
            type="text"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://www.basketball-reference.com/boxscores/pbp/..."
          />
          <button className="btn-small" onClick={handleFindUrl} disabled={!selectedVideo}>Find URL</button>
        </div>
        <p className="info-text">
          Find URL tries to match the game on Basketball Reference from the video title.
        </p>
        {lookupNote && <p className="download-message failure">{lookupNote}</p>}
      </div>
      <button onClick={handleScrape} disabled={outcome === 'running'}>
        Scrape Data
      </button>
      {outcome !== 'idle' && (
        <div className="download-status" role="status">
          {outcome === 'running' && (
            <div className="download-track" aria-hidden="true">
              <div className="download-bar" />
            </div>
          )}
          <p className={`download-message ${outcome}`}>{message}</p>
        </div>
      )}
    </Section>
  );
}
