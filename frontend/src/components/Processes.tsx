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

function startedLabel(startedAt: number | null) {
  if (!startedAt) return '';
  return new Date(startedAt * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
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
          <p>Matching can take up to 30 minutes. Progress shows up here.</p>
        </div>
      </header>
      {error && <p role="alert" className="notice">{error}</p>}
      {!jobs.length && <p className="empty">No jobs yet. Start a match from Splice.</p>}
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
                    {job.started_at ? ` · started ${startedLabel(job.started_at)}` : ''}
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
              <p className="process-meta">
                {job.progress
                  ? `${job.progress.done} of ${job.progress.total} plays matched`
                  : job.status === 'running'
                    ? 'Working. A match can take up to 30 minutes.'
                    : job.error || ''}
              </p>
              <ProcessLog text={job.log} />
            </article>
          );
        })}
      </div>
    </>
  );
}
