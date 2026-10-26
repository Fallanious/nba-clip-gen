import { useEffect, useId, useRef, useState } from 'react';
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

function ProcessActivity({ job }: { job: ProcessJob }) {
  const [tab, setTab] = useState('highlights');
  const id = useId();
  const events = job.highlights?.events ?? [];
  const summaries = job.highlights?.summaries ?? [];
  const tabs = ['highlights', 'technical'];
  return (
    <section className="process-activity" aria-label="Process activity">
      <div className="tabs" role="tablist" aria-label="Log view">
        {tabs.map((name, index) => (
          <button key={name} id={`${id}-${name}`} role="tab"
            className={tab === name ? 'selected' : 'quiet'}
            aria-selected={tab === name} aria-controls={`${id}-${name}-panel`}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)}
            onKeyDown={event => {
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1
                : event.key === 'ArrowRight' || event.key === 'ArrowLeft' ? 1 - index : null;
              if (next !== null) {
                event.preventDefault();
                setTab(tabs[next]);
                document.getElementById(`${id}-${tabs[next]}`)?.focus();
              }
            }}>
            {name === 'highlights' ? 'Highlights' : 'Technical log'}
          </button>
        ))}
      </div>
      <div id={`${id}-highlights-panel`} role="tabpanel" aria-labelledby={`${id}-highlights`} hidden={tab !== 'highlights'}>
        {job.error && <p className="notice">{job.error}</p>}
        {summaries.map((item, i) => (
          <details className={`process-summary ${item.level}`} key={i}>
            <summary>{item.title}</summary><p>{item.detail}</p>
          </details>
        ))}
        {events.length ? <>
          <p className="process-meta">Latest activity first · Up to 20 highlights</p>
          <ul className="process-highlights">
            {events.map((item, i) => <li key={i} className={item.level}>
              <span className="highlight-mark" aria-label={item.level}>{item.level === 'success' ? '✓' : item.level === 'error' ? '!' : '·'}</span>
              <div><strong>{item.title}</strong>{item.detail && <p>{item.detail}</p>}</div>
            </li>)}
          </ul>
        </> : <p className="process-meta process-empty">
          {job.status === 'running' ? 'Waiting for the first highlight. Technical log shows live output.' : 'No highlights recorded. See Technical log for output.'}
        </p>}
      </div>
      <div id={`${id}-technical-panel`} role="tabpanel" aria-labelledby={`${id}-technical`} hidden={tab !== 'technical'}>
        <p className="process-meta">Most recent 80 lines of raw output</p>
        {tab === 'technical' && <ProcessLog text={job.log} />}
      </div>
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
              <p className="process-meta">
                {job.progress
                  ? `${job.progress.done} of ${job.progress.total} plays matched`
                  : job.status === 'running'
                    ? 'Working. A match can take up to 30 minutes.'
                    : job.error || ''}
              </p>
              <ProcessActivity job={job} />
            </article>
          );
        })}
      </div>
    </>
  );
}
