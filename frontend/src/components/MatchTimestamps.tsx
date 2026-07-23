import { useState, useRef, useCallback } from 'react';
import { startMatch, fetchSuggestedPBP, stopJob } from '../api';
import { useJob } from '../hooks/useJob';
import { useMultipleJobs } from '../hooks/useJob';
import type { Video, PlayByPlayFile } from '../types';
import Section from './Section';
import OutputArea from './OutputArea';

interface Props {
  videos: Video[];
  pbpFiles: PlayByPlayFile[];
  onRefreshVideos: () => void;
  onRefreshPBP: () => void;
  onComplete: () => void;
}

export default function MatchTimestamps({
  videos, pbpFiles, onRefreshVideos, onRefreshPBP, onComplete,
}: Props) {
  const [video, setVideo] = useState('');
  const [pbp, setPbp] = useState('');
  const [pbpHint, setPbpHint] = useState('');
  const [pbpHintColor, setPbpHintColor] = useState('#666');
  const [buffer, setBuffer] = useState(3);
  const [interval, setInterval_] = useState(2);
  const [maxPlays, setMaxPlays] = useState(50);
  const [startTime, setStartTime] = useState(50);
  const [workers, setWorkers] = useState(1);
  const [isRunning, setIsRunning] = useState(false);

  const jobIds = useRef<string[]>([]);
  const singleJob = useJob();
  const multiJob = useMultipleJobs();

  const activeJob = workers > 1 ? multiJob : singleJob;

  const handleVideoChange = useCallback(async (value: string) => {
    setVideo(value);
    if (!value) {
      setPbpHint('');
      return;
    }
    try {
      const data = await fetchSuggestedPBP(value);
      if (data.suggested) {
        setPbp(data.suggested);
        setPbpHint(`Auto-selected: ${data.game_info}`);
        setPbpHintColor('#2ed573');
      } else if (data.expected) {
        setPbpHint(data.reason || '');
        setPbpHintColor('#f39c12');
      } else {
        setPbpHint(data.reason || 'Could not determine matching file');
        setPbpHintColor('#666');
      }
    } catch {
      setPbpHint('');
    }
  }, []);

  const handleStart = async () => {
    if (!video || !pbp) return;
    setIsRunning(true);

    try {
      const data = await startMatch({
        video, playbyplay: pbp, buffer,
        sample_interval: interval, max_plays: maxPlays,
        start_time: startTime, num_workers: workers,
      });
      if (data.error) {
        setIsRunning(false);
        return;
      }

      jobIds.current = data.job_ids || [data.job_id];

      const done = () => {
        setIsRunning(false);
        jobIds.current = [];
        onComplete();
      };

      if (data.job_ids && data.job_ids.length > 1) {
        multiJob.startPolling(data.job_ids, done);
      } else {
        singleJob.startPolling(data.job_id, done);
      }
    } catch {
      setIsRunning(false);
    }
  };

  const handleStop = async () => {
    try {
      await Promise.all(jobIds.current.map(id => stopJob(id)));
      setIsRunning(false);
      jobIds.current = [];
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <Section title="3. Match Timestamps">
      <div className="form-group">
        <label>Video File</label>
        <div className="form-row">
          <select value={video} onChange={e => handleVideoChange(e.target.value)}>
            <option value="">-- Select Video --</option>
            {videos.map(v => (
              <option key={v.path} value={v.path}>
                {v.name} ({v.size_mb} MB)
              </option>
            ))}
          </select>
          <button className="btn-small" onClick={onRefreshVideos}>Refresh</button>
        </div>
      </div>

      <div className="form-group">
        <label>Play-by-Play File</label>
        <div className="form-row">
          <select value={pbp} onChange={e => setPbp(e.target.value)}>
            <option value="">-- Select Play-by-Play --</option>
            {pbpFiles.map(f => (
              <option key={f.path} value={f.path}>{f.name}</option>
            ))}
          </select>
          <button className="btn-small" onClick={onRefreshPBP}>Refresh</button>
        </div>
        {pbpHint && <p className="info-text" style={{ color: pbpHintColor }}>{pbpHint}</p>}
      </div>

      <div className="form-row-3">
        <div className="form-group">
          <label>Buffer (sec)</label>
          <input type="number" value={buffer} onChange={e => setBuffer(+e.target.value)} min={1} max={10} />
        </div>
        <div className="form-group">
          <label>Sample Interval</label>
          <input type="number" value={interval} onChange={e => setInterval_(+e.target.value)} min={1} max={10} />
        </div>
        <div className="form-group">
          <label>Max Plays</label>
          <input type="number" value={maxPlays} onChange={e => setMaxPlays(+e.target.value)} min={1} max={200} />
        </div>
      </div>

      <div className="form-row-3">
        <div className="form-group">
          <label>Start Time (sec)</label>
          <input type="number" value={startTime} onChange={e => setStartTime(+e.target.value)} min={0} />
        </div>
        <div className="form-group">
          <label>Parallel Workers</label>
          <input type="number" value={workers} onChange={e => setWorkers(+e.target.value)} min={1} max={4} />
        </div>
        <div className="form-group" />
      </div>

      <div className="form-row">
        {!isRunning ? (
          <button onClick={handleStart}>Match Timestamps</button>
        ) : (
          <button className="btn-stop" onClick={handleStop}>Stop</button>
        )}
      </div>

      <OutputArea text={activeJob.output} status={activeJob.status} />
    </Section>
  );
}
