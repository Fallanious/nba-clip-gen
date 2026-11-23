import { useEffect, useRef, useState } from 'react';
import { fetchPlayByPlay, fetchBBRefUrl, startScrape } from '../api';
import { useJob } from '../hooks/useJob';
import type { Video } from '../types';
import Section from './Section';

interface Props {
  videos: Video[];
  onComplete: () => void;
}


export default function ScrapePlayByPlay({ videos, onComplete }: Props) {
  const [selectedVideo, setSelectedVideo] = useState('');
  const [url, setUrl] = useState('');
  const [lookupNote, setLookupNote] = useState('');
  const [outcome, setOutcome] = useState<'idle' | 'running' | 'success' | 'failure'>('idle');
  const [message, setMessage] = useState('');
  const job = useJob();
  const checking = useRef(false);

  useEffect(() => {
    if (job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') return;
    if (checking.current) return;
    checking.current = true;

    const confirmSaved = async () => {
      try {
        if (job.status !== 'completed') {
          setOutcome('failure');
          setMessage(job.output.trim() || 'Fetch failed.');
          return;
        }
        const path = job.output.match(/^Saved (playbyplay\/nba\/\w+\.json)$/m)?.[1];
        const files = await fetchPlayByPlay();
        const saved = files.find(file => file.path === path && file.size > 2);
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
  }, [job.status, job.output, onComplete]);

  const handleFindUrl = async () => {
    if (!selectedVideo) return;
    setLookupNote('');
    try {
      const data = await fetchBBRefUrl(selectedVideo);
      if (data.bbref_id) {
        setUrl(data.bbref_id);
        return;
      }
      setLookupNote("Couldn't match this title to a game key.");
    } catch {
      setLookupNote("Couldn't match this title to a game key.");
    }
  };

  const handleScrape = async () => {
    if (!url.trim() || outcome === 'running') return;
    checking.current = false;
    setOutcome('running');
    setMessage('Fetching NBA play-by-play...');
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
    <Section title="1. Fetch NBA Play-by-Play">
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
        <label>NBA game ID or local game key</label>
        <div className="form-row">
          <input
            type="text"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="0022500209 or 202511110PHI"
          />
          <button className="btn-small" onClick={handleFindUrl} disabled={!selectedVideo}>Find game</button>
        </div>
        <p className="info-text">
          Find game reads the date and home team from the video title.
        </p>
        {lookupNote && <p className="download-message failure">{lookupNote}</p>}
      </div>
      <button onClick={handleScrape} disabled={outcome === 'running'}>
        Fetch NBA data
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
