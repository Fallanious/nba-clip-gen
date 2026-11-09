import { useEffect, useRef, useState } from 'react';
import { stopJob } from '../api';
import type { ProcessJob } from '../types';

interface Props {
  jobs: ProcessJob[];
  onChanged: () => void;
}

const statusLabel: Record<ProcessJob['status'], string> = {
  running: 'Running',
  completed: 'Finished',
  failed: 'Failed',
  cancelled: 'Stopped',
};

function clockLabel(at: number | null) {
  if (!at) return '';
  return new Date(at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function ProcessLog({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);

  return (
    <pre
      className="process-log"
      ref={ref}
      onScroll={() => {
        const el = ref.current;
        if (!el) return;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
    >{text || 'Waiting for output…'}</pre>
  );
}

const progressUnit: Record<string, string> = {
  Upload: 'MB uploaded',
  'Match timestamps': 'plays matched',
};

const runningCopy: Record<string, string> = {
  Download: 'Downloading the video.',
  Upload: 'Preparing the file. Transfer progress shows once the upload starts.',
  'Match timestamps': 'Working. A match can take up to 30 minutes.',
  'Scrape play-by-play': 'Scraping the play-by-play.',
  'Generate clips': 'Cutting clips from the game.',
};

const finishedCopy: Record<string, string> = {
  Download: 'Download finished.',
  Upload: 'Upload finished.',
  'Match timestamps': 'Match finished.',
  'Scrape play-by-play': 'Scrape finished.',
  'Generate clips': 'Clips are ready.',
};

function statusCopy(job: ProcessJob) {
  if (job.progress) {
    const unit = progressUnit[job.kind];
    const count = `${job.progress.done} of ${job.progress.total}`;
    return unit ? `${count} ${unit}` : count;
  }
  if (job.status === 'running') return runningCopy[job.kind] || 'Working.';
  if (job.error) return job.error;
  if (job.status === 'cancelled') return 'Stopped.';
  if (job.status === 'completed') return finishedCopy[job.kind] || 'Finished.';
  return '';
}

function ProcessActivity({ job }: { job: ProcessJob }) {
  return (
    <section className="process-activity" aria-label="Technical log">
      {job.error && <p className="notice">{job.error}</p>}
      <ProcessLog text={job.log} />
    </section>
  );
}

export default function Processes({ jobs, onChanged }: Props) {
  const [stopping, setStopping] = useState('');
  const [error, setError] = useState('');

  const handleStop = async (id: string) => {
    setStopping(id);
    setError('');
    try {
      await stopJob(id);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not stop that job.');
    } finally {
      setStopping('');
    }
  };

  return (
    <>
      <header>
        <div>
          <p className="eyebrow">WHAT IS RUNNING</p>
          <h1>Processes</h1>
          <p>Downloads, uploads, and matches each keep their own log.</p>
        </div>
      </header>
      {error && <p role="alert" className="notice">{error}</p>}
      {!jobs.length && <p className="empty">No jobs yet. Start one from Splice or Upload.</p>}
      <div className="process-list">
        {jobs.map(job => {
          const pct = job.progress && job.progress.total
            ? Math.min(100, Math.round((job.progress.done / job.progress.total) * 100))
            : null;
          return (
            <article className="process-card" key={job.id}>
              <div className="process-head">
                <div>
                  <strong>{job.kind}</strong>
                  <p>{job.label || 'Local job'}</p>
                  <p className="process-meta">
                    <span className="process-id">{job.id}</span>
                    {job.started_at ? ` · started ${clockLabel(job.started_at)}` : ''}
                    {job.finished_at ? ` · ended ${clockLabel(job.finished_at)}` : ''}
                  </p>
                </div>
                <div className="process-actions">
                  <span className={`status-pill ${job.status}`}>{statusLabel[job.status]}</span>
                  {job.status === 'running' && (
                    <button className="quiet" disabled={stopping === job.id} onClick={() => void handleStop(job.id)}>
                      {stopping === job.id ? 'Stopping…' : 'Stop'}
                    </button>
                  )}
                </div>
              </div>
              {job.status === 'running' && (
                <div className="process-track" aria-label={pct == null ? 'Working' : `${pct}%`}>
                  {pct == null
                    ? <div className="download-bar" />
                    : <div className="process-fill" style={{ width: `${pct}%` }} />}
                </div>
              )}
              <p className="process-meta">{statusCopy(job)}</p>
              <ProcessActivity job={job} />
            </article>
          );
        })}
      </div>
    </>
  );
}
