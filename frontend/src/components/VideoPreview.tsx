import { useEffect, useRef, useCallback, useState } from 'react';
import { getVideoUrl } from '../api';
import type { Play } from '../types';

interface Props {
  play: Play;
  videoPath: string;
  onClose: () => void;
}

function formatTimePrecise(seconds: number): string {
  if (seconds == null || isNaN(seconds)) return '0:00.0';
  const m = Math.floor(seconds / 60);
  const s = (seconds % 60).toFixed(1);
  return `${m}:${s.padStart(4, '0')}`;
}

function formatTime(seconds: number): string {
  if (seconds == null || isNaN(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function VideoPreview({ play, videoPath, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);
  const stopHandlerRef = useRef<(() => void) | null>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [timeText, setTimeText] = useState('0:00.0 / 0:00.0');

  const clipStart = play.video_start;
  const clipEnd = play.video_end;
  const clipDuration = clipEnd - clipStart;

  const updateProgress = useCallback((elapsed: number) => {
    const pct = Math.min(100, Math.max(0, (elapsed / clipDuration) * 100));
    if (barRef.current) barRef.current.style.width = `${pct}%`;
    setTimeText(`${formatTimePrecise(elapsed)} / ${formatTimePrecise(clipDuration)}`);
  }, [clipDuration]);

  const startProgressLoop = useCallback(() => {
    const loop = () => {
      const vid = videoRef.current;
      if (vid && !vid.paused) {
        const elapsed = vid.currentTime - clipStart;
        updateProgress(elapsed);
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  }, [clipStart, updateProgress]);

  const stopProgressLoop = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const attachStopHandler = useCallback(() => {
    const vid = videoRef.current;
    if (!vid) return;

    // Remove old handler
    if (stopHandlerRef.current) {
      vid.removeEventListener('timeupdate', stopHandlerRef.current);
    }

    const handler = () => {
      if (vid.currentTime >= clipEnd) {
        vid.pause();
        vid.removeEventListener('timeupdate', handler);
        setIsPlaying(false);
        updateProgress(clipDuration);
        stopProgressLoop();
      }
    };
    stopHandlerRef.current = handler;
    vid.addEventListener('timeupdate', handler);
  }, [clipEnd, clipDuration, updateProgress, stopProgressLoop]);

  // Load video and start playback when play changes
  useEffect(() => {
    const vid = videoRef.current;
    if (!vid) return;

    stopProgressLoop();
    vid.pause();
    if (stopHandlerRef.current) {
      vid.removeEventListener('timeupdate', stopHandlerRef.current);
    }

    const src = getVideoUrl(videoPath, clipStart, clipEnd);
    vid.src = src;
    vid.load();

    updateProgress(0);
    setIsPlaying(false);

    const onSeeked = () => {
      vid.removeEventListener('seeked', onSeeked);
      attachStopHandler();
      startProgressLoop();
      vid.play().catch(() => { /* autoplay blocked */ });
      setIsPlaying(true);
    };

    vid.addEventListener('seeked', onSeeked);
    vid.currentTime = clipStart;

    return () => {
      stopProgressLoop();
      vid.removeEventListener('seeked', onSeeked);
      if (stopHandlerRef.current) {
        vid.removeEventListener('timeupdate', stopHandlerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [play, videoPath]);

  const togglePlayback = useCallback(() => {
    const vid = videoRef.current;
    if (!vid) return;

    if (vid.paused) {
      if (vid.currentTime >= clipEnd - 0.1) {
        vid.currentTime = clipStart;
      }
      attachStopHandler();
      startProgressLoop();
      vid.play().catch(() => { });
      setIsPlaying(true);
    } else {
      vid.pause();
      stopProgressLoop();
      setIsPlaying(false);
    }
  }, [clipStart, clipEnd, attachStopHandler, startProgressLoop, stopProgressLoop]);

  const replay = useCallback(() => {
    const vid = videoRef.current;
    if (!vid) return;

    vid.pause();
    stopProgressLoop();
    vid.currentTime = clipStart;
    updateProgress(0);

    attachStopHandler();
    startProgressLoop();
    vid.play().catch(() => { });
    setIsPlaying(true);
  }, [clipStart, attachStopHandler, startProgressLoop, stopProgressLoop, updateProgress]);

  const handleScrub = useCallback((e: React.MouseEvent) => {
    const wrapper = progressRef.current;
    const vid = videoRef.current;
    if (!wrapper || !vid) return;

    const rect = wrapper.getBoundingClientRect();
    const pct = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetTime = clipStart + pct * clipDuration;
    vid.currentTime = targetTime;
    updateProgress(pct * clipDuration);
  }, [clipStart, clipDuration, updateProgress]);

  // Mouse drag scrubbing
  useEffect(() => {
    let isDragging = false;

    const onMouseDown = (e: MouseEvent) => {
      const wrapper = progressRef.current;
      if (!wrapper || !wrapper.contains(e.target as Node)) return;
      isDragging = true;
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      const wrapper = progressRef.current;
      const vid = videoRef.current;
      if (!wrapper || !vid) return;
      const rect = wrapper.getBoundingClientRect();
      const pct = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      vid.currentTime = clipStart + pct * clipDuration;
      updateProgress(pct * clipDuration);
    };

    const onMouseUp = () => { isDragging = false; };

    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, [clipStart, clipDuration, updateProgress]);

  return (
    <div style={{ marginBottom: 15 }}>
      <div style={{ position: 'relative', background: '#000', borderRadius: '6px', overflow: 'hidden' }}>
        <video
          ref={videoRef}
          style={{ width: '100%', maxHeight: 360, display: 'block', cursor: 'pointer' }}
          onClick={togglePlayback}
        />
      </div>
      <div className="clip-controls">
        <button className="btn-play" onClick={togglePlayback}>
          {isPlaying ? '\u25AE\u25AE' : '\u25B6'}
        </button>
        <button className="btn-play" onClick={replay} title="Replay">&#8634;</button>
        <div
          className="clip-progress-wrapper"
          ref={progressRef}
          onClick={handleScrub}
        >
          <div className="clip-progress-bar" ref={barRef} />
        </div>
        <span className="clip-time">{timeText}</span>
        <span className="clip-label-badge">{play.primary_action || 'other'}</span>
        <button className="btn-small" onClick={onClose} style={{ marginLeft: 'auto' }}>Close</button>
      </div>
      <div style={{ marginTop: 4 }}>
        <span style={{ fontSize: 12, color: '#888' }}>
          Play {play.play_index}: {play.team} Q{play.quarter} {play.game_time} | Video {formatTime(clipStart)} - {formatTime(clipEnd)} ({clipDuration.toFixed(1)}s)
        </span>
      </div>
    </div>
  );
}
