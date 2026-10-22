import { useState, useRef, useCallback, useEffect } from 'react';
import { startMatch, fetchSuggestedPBP, getVideoUrl } from '../api';
import type { Video, PlayByPlayFile } from '../types';
import Section from './Section';

interface Props {
  videos: Video[];
  pbpFiles: PlayByPlayFile[];
  onRefreshVideos: () => void;
  onRefreshPBP: () => void;
}

export default function MatchTimestamps({
  videos, pbpFiles, onRefreshVideos, onRefreshPBP,
}: Props) {
  const [video, setVideo] = useState('');
  const [pbp, setPbp] = useState('');
  const [pbpHint, setPbpHint] = useState('');
  const [pbpHintColor, setPbpHintColor] = useState('#666');
  const [buffer, setBuffer] = useState<number | ''>(3);
  const [interval, setInterval_] = useState<number | ''>(2);
  const [maxPlays, setMaxPlays] = useState<number | ''>(50);
  const [startTime, setStartTime] = useState<number | ''>(50);
  const [workers, setWorkers] = useState<number | ''>(1);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState('');
  const [noticeOk, setNoticeOk] = useState(true);

  const playerRef = useRef<HTMLVideoElement>(null);
  const seekFromInput = useRef(false);

  const setOptionalNumber = (raw: string, set: (value: number | '') => void) => {
    if (raw === '') {
      set('');
      return;
    }
    const next = Number(raw);
    if (!Number.isNaN(next)) set(next);
  };

  useEffect(() => {
    const player = playerRef.current;
    if (!player || !video || startTime === '') return;
    const seek = () => {
      if (Math.abs(player.currentTime - startTime) < 0.4) return;
      seekFromInput.current = true;
      player.currentTime = startTime;
    };
    if (player.readyState >= 1) seek();
    else player.addEventListener('loadedmetadata', seek, { once: true });
  }, [video, startTime]);

  const handlePlayerSeek = () => {
    const player = playerRef.current;
    if (!player) return;
    if (seekFromInput.current) {
      seekFromInput.current = false;
      return;
    }
    setStartTime(Math.max(0, Math.round(player.currentTime)));
  };

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
    if (!video || !pbp || starting) return;
    setStarting(true);
    setNotice('');

    try {
      const data = await startMatch({
        video, playbyplay: pbp,
        buffer: buffer === '' ? 3 : buffer,
        sample_interval: interval === '' ? 2 : interval,
        max_plays: maxPlays === '' ? 50 : maxPlays,
        start_time: startTime === '' ? 0 : startTime,
        num_workers: workers === '' ? 1 : workers,
      });
      const ids = data.job_ids?.length ? data.job_ids : [data.job_id];
      setNoticeOk(true);
      setNotice(
        `Matching is running. Job ${ids.join(', ')}. This can take up to 30 minutes. Follow progress on the Processes tab.`
      );
    } catch (err) {
      setNoticeOk(false);
      setNotice(err instanceof Error ? err.message : 'Could not start matching.');
    } finally {
      setStarting(false);
    }
  };

  return (
    <Section title="3. Match Timestamps">
      <div className="match-layout">
        <div className="match-preview">
          {video ? (
            <video
              key={video}
              ref={playerRef}
              className="match-player"
              controls
              preload="metadata"
              src={getVideoUrl(video)}
              onSeeked={handlePlayerSeek}
            />
          ) : (
            <div className="match-player match-player-empty">Select a video to preview the full game.</div>
          )}
          <p className="info-text">Scrub to the first frame of live play. Start time skips the intro before that.</p>
        </div>
        <div>
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
              <input type="number" value={buffer} onChange={e => setOptionalNumber(e.target.value, setBuffer)} min={1} max={10} />
            </div>
            <div className="form-group">
              <label>Sample Interval</label>
              <input type="number" value={interval} onChange={e => setOptionalNumber(e.target.value, setInterval_)} min={1} max={10} />
            </div>
            <div className="form-group">
              <label>Max Plays</label>
              <input type="number" value={maxPlays} onChange={e => setOptionalNumber(e.target.value, setMaxPlays)} min={1} max={200} />
            </div>
          </div>

          <div className="form-row-3">
            <div className="form-group">
              <label>Start Time (sec)</label>
              <input type="number" value={startTime} onChange={e => setOptionalNumber(e.target.value, setStartTime)} min={0} />
              <p className="info-text">First second of the game.</p>
            </div>
            <div className="form-group">
              <label>Parallel Workers</label>
              <input type="number" value={workers} onChange={e => setOptionalNumber(e.target.value, setWorkers)} min={1} max={4} />
            </div>
            <div className="form-group" />
          </div>

          <div className="form-row">
            <button onClick={handleStart} disabled={starting || !video || !pbp}>
              {starting ? 'Starting…' : 'Match Timestamps'}
            </button>
          </div>
          {notice && <p className={`download-message ${noticeOk ? 'success' : 'failure'}`}>{notice}</p>}
        </div>
      </div>
    </Section>
  );
}
