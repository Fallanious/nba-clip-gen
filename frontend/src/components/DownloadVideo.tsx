import { useEffect, useRef, useState } from 'react';
import { fetchVideos, startDownload } from '../api';
import { useJob } from '../hooks/useJob';
import Section from './Section';

interface Props {
  onComplete: () => void;
}

export default function DownloadVideo({ onComplete }: Props) {
  const [url, setUrl] = useState('');
  const [outcome, setOutcome] = useState<'idle' | 'running' | 'success' | 'failure'>('idle');
  const [message, setMessage] = useState('');
  const job = useJob();
  const started = useRef(0);
  const checking = useRef(false);

  useEffect(() => {
    if (job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') return;
    if (checking.current) return;
    checking.current = true;

    const confirmSaved = async () => {
      try {
        const videos = await fetchVideos();
        const saved = videos
          .filter(video => video.mtime * 1000 >= started.current - 2000 && video.size_mb > 0)
          .sort((a, b) => b.mtime - a.mtime)[0];
        if (saved) {
          setOutcome('success');
          setMessage(`Saved to film/${saved.name}`);
          onComplete();
          return;
        }
      } catch {
        setOutcome('failure');
        setMessage('Download failed. Could not check the film folder.');
        return;
      }
      setOutcome('failure');
      setMessage('Download failed. Nothing new was saved to film.');
    };

    void confirmSaved();
  }, [job.status, onComplete]);

  const handleDownload = async () => {
    if (!url.trim() || job.status === 'running') return;
    checking.current = false;
    started.current = Date.now();
    setOutcome('running');
    setMessage('Downloading...');
    try {
      const data = await startDownload(url.trim());
      if (data.error) {
        setOutcome('failure');
        setMessage(data.error);
        return;
      }
      job.startPolling(data.job_id);
    } catch (err) {
      setOutcome('failure');
      setMessage(err instanceof Error ? err.message : 'Download failed.');
    }
  };

  return (
    <Section title="1. Download Video">
      <div className="form-group">
        <label>YouTube URL</label>
        <input
          type="text"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
        />
      </div>
      <button onClick={handleDownload} disabled={outcome === 'running'}>
        Download
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
