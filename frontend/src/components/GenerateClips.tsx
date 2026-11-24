import { useState } from 'react';
import { startGenerate } from '../api';
import { useJob } from '../hooks/useJob';
import type { Video, TimestampFile } from '../types';
import Section from './Section';
import OutputArea from './OutputArea';

interface Props {
  video: string;
  chooseVideo: (path: string) => void;
  timestamps: string;
  chooseFile: (path: string) => void;
  videos: Video[];
  timestampFiles: TimestampFile[];
  onRefreshTimestamps: () => void;
}

export default function GenerateClips({ videos, video, chooseVideo, timestamps, chooseFile, timestampFiles, onRefreshTimestamps }: Props) {
  const [error, setError] = useState('');
  const [noAudio, setNoAudio] = useState(true);
  const job = useJob();

  const handleGenerate = async () => {
    if (!video || !timestamps) return;
    setError('');
    try {
      const data = await startGenerate({
        video,
        timestamps,
        no_audio: noAudio,
      });
      if (data.error) throw new Error(data.error);
      job.startPolling(data.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not generate clips.');
    }
  };

  return (
    <Section title="4. Generate Clips">
      <div className="form-group">
        <label>Video File</label>
        <select value={video} onChange={e => chooseVideo(e.target.value)}>
          <option value="">-- Select Video --</option>
          {videos.map(v => (
            <option key={v.path} value={v.path}>
              {v.name} ({v.size_mb} MB)
            </option>
          ))}
        </select>
      </div>
      <div className="form-group">
        <label>Timestamps File</label>
        <div className="form-row">
          <select value={timestamps} onChange={e => chooseFile(e.target.value)}>
            <option value="">-- Select Timestamps --</option>
            {timestampFiles.map(f => (
              <option key={f.path} value={f.path}>
                {f.name} ({f.play_count} plays)
              </option>
            ))}
          </select>
          <button className="btn-small" onClick={onRefreshTimestamps}>Refresh</button>
        </div>
      </div>
      <div className="form-group">
        <div className="checkbox-group">
          <input type="checkbox" id="gen-noaudio" checked={noAudio} onChange={e => setNoAudio(e.target.checked)} />
          <label htmlFor="gen-noaudio">No audio (faster)</label>
        </div>
      </div>
      <button onClick={handleGenerate} disabled={!video || !timestamps || job.status === 'running'}>
        Generate Clips
      </button>
      <OutputArea text={error || job.output} status={error ? 'failed' : job.status} />
    </Section>
  );
}
