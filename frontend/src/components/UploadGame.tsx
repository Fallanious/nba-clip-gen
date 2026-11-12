import { useState } from 'react';
import { startUpload } from '../api';
import type { Video } from '../types';

interface Props {
  videos: Video[];
  onStarted?: () => void;
}

const title = (name: string) => name.replace(/\.(mp4|mkv|webm)$/i, '').trim();

export default function UploadGame({ videos, onStarted }: Props) {
  const [video, setVideo] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [ok, setOk] = useState(true);
  const chosen = videos.find(item => item.path === video);

  const handleUpload = async () => {
    if (!video || busy) return;
    setBusy(true);
    setOk(true);
    setMessage('Starting upload...');
    try {
      const data = await startUpload(video);
      if (data.error) {
        setOk(false);
        setMessage(data.error);
        return;
      }
      setMessage(`Upload is running. Job ${data.job_id}. Follow it on the Processes tab.`);
      onStarted?.();
    } catch (err) {
      setOk(false);
      setMessage(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-game">
      <h2>Upload</h2>
      <div className="form-group">
        <label htmlFor="upload-video">Film file</label>
        <select id="upload-video" value={video} onChange={e => { setVideo(e.target.value); setMessage(''); }}>
          <option value="">-- Select Video --</option>
          {videos.map(item => (
            <option key={item.path} value={item.path}>{title(item.name)} ({item.size_mb} MB)</option>
          ))}
        </select>
      </div>
      {chosen?.parsed?.bbref_id && <p className="file-note">Saves as games/{chosen.parsed.bbref_id}.mkv</p>}
      {chosen && !chosen.parsed?.bbref_id && <p className="download-message failure">This filename needs both teams and a date, like TEAM at TEAM | FULL GAME HIGHLIGHTS | Month D, YYYY.</p>}
      <button onClick={handleUpload} disabled={!chosen?.parsed?.bbref_id || busy}>
        {busy ? 'Starting…' : 'Upload'}
      </button>
      {message && <p className={`download-message ${ok ? 'success' : 'failure'}`}>{message}</p>}
      {!videos.length && <p className="empty">No local footage yet. Add a file to the film folder first.</p>}
    </div>
  );
}
