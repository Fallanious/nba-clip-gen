import { useEffect, useRef } from 'react';

interface OutputAreaProps {
  text: string;
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';
}

export default function OutputArea({ text, status }: OutputAreaProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [text]);

  if (status === 'idle' && !text) return null;

  const statusClass = status === 'completed' ? 'success'
    : status === 'failed' || status === 'cancelled' ? 'error'
      : status;

  return (
    <div ref={ref} className={`output ${statusClass}`}>
      {text || '\u00A0'}
    </div>
  );
}
