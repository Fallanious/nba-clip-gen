import { useState } from 'react';
import { startDownload } from '../api';
import { useJob } from '../hooks/useJob';
import Section from './Section';
import OutputArea from './OutputArea';

interface Props {
  onComplete: () => void;
}

export default function DownloadVideo({ onComplete }: Props) {
  const [url, setUrl] = useState('');
  const job = useJob();

  const handleDownload = async () => {
    if (!url.trim()) {
      return;
    }
    try {
      const data = await startDownload(url.trim());
      if (data.error) return;
      job.startPolling(data.job_id, onComplete);
    } catch (err) {
      console.error(err);
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
      <button onClick={handleDownload} disabled={job.status === 'running'}>
        Download
      </button>
      <OutputArea text={job.output} status={job.status} />
    </Section>
  );
}
