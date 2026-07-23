import { useState } from 'react';
import { startScrape, fetchBBRefUrl } from '../api';
import { useJob } from '../hooks/useJob';
import type { Video } from '../types';
import Section from './Section';
import OutputArea from './OutputArea';

interface Props {
  videos: Video[];
  onComplete: () => void;
}

export default function ScrapePlayByPlay({ videos, onComplete }: Props) {
  const [selectedVideo, setSelectedVideo] = useState('');
  const [url, setUrl] = useState('');
  const job = useJob();

  const handleFindUrl = async () => {
    if (!selectedVideo) return;
    try {
      const data = await fetchBBRefUrl(selectedVideo);
      if (data.error) {
        return;
      }
      if (data.bbref_url) {
        setUrl(data.bbref_url);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleScrape = async () => {
    if (!url.trim()) return;
    try {
      const data = await startScrape(url.trim());
      if (data.error) return;
      job.startPolling(data.job_id, onComplete);
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <Section title="2. Scrape Play-by-Play">
      <div className="form-group">
        <label>Auto-detect from Video</label>
        <div className="form-row">
          <select value={selectedVideo} onChange={e => setSelectedVideo(e.target.value)}>
            <option value="">-- Select Video --</option>
            {videos.map(v => (
              <option key={v.path} value={v.path}>
                {v.name} ({v.size_mb} MB)
              </option>
            ))}
          </select>
          <button className="btn-small" onClick={handleFindUrl}>Find URL</button>
        </div>
      </div>
      <div className="form-group">
        <label>Basketball Reference URL</label>
        <input
          type="text"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://www.basketball-reference.com/boxscores/pbp/..."
        />
        <p className="info-text">
          Example: https://www.basketball-reference.com/boxscores/pbp/202601030NYK.html
        </p>
      </div>
      <button onClick={handleScrape} disabled={job.status === 'running'}>
        Scrape Data
      </button>
      <OutputArea text={job.output} status={job.status} />
    </Section>
  );
}
