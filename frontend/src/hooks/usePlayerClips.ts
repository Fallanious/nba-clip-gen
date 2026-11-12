import { useCallback, useRef, useState } from 'react';
import { fetchPlayerClips } from '../api';
import type { BrowserPlayer, PlayerClip, PlayerClipRole } from '../types';

export function usePlayerClips() {
  const [selected, setSelected] = useState<BrowserPlayer | null>(null);
  const [clips, setClips] = useState<PlayerClip[]>([]);
  const [roles, setRoles] = useState<PlayerClipRole[]>([]);
  const [role, setRole] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);
  const cursorRef = useRef<number | null>(null);
  const loadingMoreRef = useRef(false);
  const selectedRef = useRef<BrowserPlayer | null>(null);
  const roleRef = useRef('');

  function remember(nextCursor: number | null, nextRole: string) {
    cursorRef.current = nextCursor;
    roleRef.current = nextRole;
    setCursor(nextCursor);
    setRole(nextRole);
  }

  async function loadFirst(player: BrowserPlayer, nextRole: string, token: number) {
    setLoading(true);
    try {
      const data = await fetchPlayerClips(player.id, { role: nextRole });
      if (requestId.current !== token) return;
      setRoles(data.roles);
      setClips(data.clips);
      remember(data.cursor, nextRole);
    } catch (err) {
      if (requestId.current !== token) return;
      setError(err instanceof Error ? err.message : 'Could not load plays.');
    } finally {
      if (requestId.current === token) setLoading(false);
    }
  }

  async function open(player: BrowserPlayer) {
    const token = ++requestId.current;
    loadingMoreRef.current = false;
    selectedRef.current = player;
    setSelected(player);
    setClips([]);
    setRoles([]);
    setError('');
    setLoadingMore(false);
    remember(null, '');
    await loadFirst(player, '', token);
  }

  function chooseRole(nextRole: string) {
    const player = selectedRef.current;
    if (!player || nextRole === roleRef.current) return;
    const token = ++requestId.current;
    loadingMoreRef.current = false;
    setClips([]);
    setError('');
    setLoadingMore(false);
    remember(null, nextRole);
    void loadFirst(player, nextRole, token);
  }

  const loadMore = useCallback(async () => {
    const player = selectedRef.current;
    const after = cursorRef.current;
    if (!player || after == null || loadingMoreRef.current) return;
    const token = requestId.current;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const data = await fetchPlayerClips(player.id, { cursor: after, role: roleRef.current });
      if (requestId.current !== token) return;
      setClips(prev => {
        const seen = new Set(prev.map(clip => `${clip.id}:${clip.role}`));
        return [...prev, ...data.clips.filter(clip => !seen.has(`${clip.id}:${clip.role}`))];
      });
      remember(data.cursor, roleRef.current);
    } catch (err) {
      if (requestId.current !== token) return;
      setError(err instanceof Error ? err.message : 'Could not load plays.');
    } finally {
      if (requestId.current === token) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, []);

  function close() {
    requestId.current += 1;
    loadingMoreRef.current = false;
    selectedRef.current = null;
    setSelected(null);
    setClips([]);
    setRoles([]);
    setError('');
    setLoading(false);
    setLoadingMore(false);
    remember(null, '');
  }

  const shown = clips.filter(clip => !role || clip.role === role);

  return {
    selected,
    clips,
    shown,
    roles,
    role,
    loading,
    loadingMore,
    hasMore: cursor !== null,
    error,
    open,
    close,
    chooseRole,
    loadMore,
  };
}
