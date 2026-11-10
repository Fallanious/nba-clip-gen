import { useEffect, useRef, useState } from 'react';
import { fetchPlayerClip } from '../api';
import type { PlayerClip } from '../types';

export function usePlayerClip() {
  const [active, setActive] = useState<PlayerClip | null>(null);
  const [url, setUrl] = useState('');
  const [cutting, setCutting] = useState(false);
  const [error, setError] = useState('');
  const urlRef = useRef('');
  const requestId = useRef(0);

  function release() {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = '';
    setUrl('');
  }

  function clear() {
    requestId.current += 1;
    setActive(null);
    setCutting(false);
    setError('');
    release();
  }

  function play(playerId: number, clip: PlayerClip) {
    if (active?.id === clip.id && urlRef.current) return;
    const token = ++requestId.current;
    setError('');
    setCutting(true);
    setActive(clip);
    release();
    void fetchPlayerClip(playerId, clip.id)
      .then(blob => {
        if (requestId.current !== token) return;
        const next = URL.createObjectURL(blob);
        urlRef.current = next;
        setUrl(next);
      })
      .catch(err => {
        if (requestId.current !== token) return;
        setError(err instanceof Error ? err.message : 'Could not cut this clip.');
      })
      .finally(() => {
        if (requestId.current === token) setCutting(false);
      });
  }

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  return { active, url, cutting, error, play, clear };
}
