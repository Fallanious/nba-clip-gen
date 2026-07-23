import { useEffect, useRef, useState, useCallback } from 'react';
import { fetchJob } from '../api';
import type { Job } from '../types';

type JobStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';

interface UseJobResult {
  status: JobStatus;
  output: string;
  error: string | null;
  startPolling: (jobId: string, onComplete?: () => void) => void;
  reset: () => void;
}

export function useJob(): UseJobResult {
  const [status, setStatus] = useState<JobStatus>('idle');
  const [output, setOutput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onCompleteRef = useRef<(() => void) | undefined>(undefined);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const startPolling = useCallback((jobId: string, onComplete?: () => void) => {
    cleanup();
    setStatus('running');
    setOutput('Running...');
    setError(null);
    onCompleteRef.current = onComplete;

    const poll = async () => {
      try {
        const data: Job = await fetchJob(jobId);
        setOutput(data.output || 'Running...');
        setStatus(data.status === 'running' ? 'running' : data.status);

        if (data.status === 'running') {
          timerRef.current = setTimeout(poll, 1000);
        } else {
          if (data.status === 'completed') {
            setOutput(data.output || 'Completed!');
            onCompleteRef.current?.();
          } else if (data.status === 'failed') {
            setOutput(data.output || data.error || 'Failed!');
            setError(data.error);
          }
        }
      } catch (err) {
        setStatus('failed');
        setError(err instanceof Error ? err.message : 'Unknown error');
      }
    };
    poll();
  }, [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    setStatus('idle');
    setOutput('');
    setError(null);
  }, [cleanup]);

  return { status, output, error, startPolling, reset };
}

// ============ Multiple Jobs (parallel workers) ============

interface UseMultipleJobsResult {
  status: JobStatus;
  output: string;
  startPolling: (jobIds: string[], onComplete?: () => void) => void;
  reset: () => void;
}

export function useMultipleJobs(): UseMultipleJobsResult {
  const [status, setStatus] = useState<JobStatus>('idle');
  const [output, setOutput] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onCompleteRef = useRef<(() => void) | undefined>(undefined);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const startPolling = useCallback((jobIds: string[], onComplete?: () => void) => {
    cleanup();
    setStatus('running');
    setOutput('Starting workers...');
    onCompleteRef.current = onComplete;

    const poll = async () => {
      try {
        const results = await Promise.all(jobIds.map(id => fetchJob(id)));

        const combinedOutput = jobIds.map((_id, i) => {
          const data = results[i];
          const statusIcon = data.status === 'completed' ? '[DONE]'
            : data.status === 'failed' ? '[FAIL]' : '[...]';
          return `=== Worker ${i + 1} ${statusIcon} ===\n${data.output || ''}`;
        }).join('\n\n');

        const allDone = results.every(r => r.status !== 'running');
        const anyFailed = results.some(r => r.status === 'failed');

        setOutput(combinedOutput);
        setStatus(allDone ? (anyFailed ? 'failed' : 'completed') : 'running');

        if (!allDone) {
          timerRef.current = setTimeout(poll, 1000);
        } else {
          onCompleteRef.current?.();
        }
      } catch (err) {
        setStatus('failed');
        setOutput(`Error: ${err instanceof Error ? err.message : 'Unknown error'}`);
      }
    };
    poll();
  }, [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    setStatus('idle');
    setOutput('');
  }, [cleanup]);

  return { status, output, startPolling, reset };
}
