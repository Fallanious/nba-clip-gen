import { useState } from 'react';
import { startDownload } from '../api';

interface Props {
  onStarted?: () => void;
}

export default function DownloadVideo({ onStarted }: Props) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [ok, setOk] = useState(true);

  const handleDownload = async () => {
    if (!url.trim() || busy) return;
    setBusy(true);
    setOk(true);
    setMessage('Starting download...');
    try {
      const data = await startDownload(url.trim());
      if (data.error) {
        setOk(false);
        setMessage(data.error);
        return;
      }
      setMessage(`Download is running. Job ${data.job_id}. Follow it on the Processes tab.`);
      onStarted?.();
    } catch (err) {
      setOk(false);
      setMessage(err instanceof Error ? err.message : 'Download failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-game">
      <h2>Download</h2>
      <div className="form-group">
        <label>YouTube URL</label>
        <input
          type="text"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
        />
      </div>
      <button onClick={handleDownload} disabled={!url.trim() || busy}>
        {busy ? 'Starting…' : 'Download'}
      </button>
      {message && <p className={`download-message ${ok ? 'success' : 'failure'}`} role="status">{message}</p>}
    </div>
  );
}
