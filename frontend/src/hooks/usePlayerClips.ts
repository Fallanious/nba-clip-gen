import { useRef, useState } from 'react';
import { fetchPlayerClips } from '../api';
import type { BrowserPlayer, PlayerClip } from '../types';

export function usePlayerClips() {
  const [selected, setSelected] = useState<BrowserPlayer | null>(null);
  const [clips, setClips] = useState<PlayerClip[]>([]);
  const [role, setRole] = useState('All plays');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);

  async function open(player: BrowserPlayer) {
    const token = ++requestId.current;
    setSelected(player);
    setRole('All plays');
    setClips([]);
    setError('');
    setLoading(true);
    try {
      const data = await fetchPlayerClips(player.id);
      if (requestId.current !== token) return;
      setClips(data.clips);
    } catch (err) {
      if (requestId.current !== token) return;
      setError(err instanceof Error ? err.message : 'Could not load plays.');
    } finally {
      if (requestId.current === token) setLoading(false);
    }
  }

  function close() {
    requestId.current += 1;
    setSelected(null);
    setClips([]);
    setRole('All plays');
    setError('');
    setLoading(false);
  }

  const roles = ['All plays', ...new Set(clips.map(clip => clip.role_label))];
  const shown = clips.filter(clip => role === 'All plays' || clip.role_label === role);

  return { selected, clips, shown, roles, role, setRole, loading, error, open, close };
}
