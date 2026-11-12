import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchPlayerLibrary } from '../api';
import type { BrowserPlayer } from '../types';

export function usePlayerLibrary() {
  const [players, setPlayers] = useState<BrowserPlayer[]>([]);
  const [season, setSeason] = useState('');
  const [teams, setTeams] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [team, setTeam] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);
  const cursorRef = useRef<number | null>(null);
  const loadingMoreRef = useRef(false);
  const queryRef = useRef('');
  const teamRef = useRef('');

  function remember(next: number | null) {
    cursorRef.current = next;
    setCursor(next);
  }

  useEffect(() => {
    const token = ++requestId.current;
    loadingMoreRef.current = false;
    queryRef.current = query;
    teamRef.current = team;
    setLoading(true);
    setLoadingMore(false);
    setPlayers([]);
    setError('');
    remember(null);
    fetchPlayerLibrary({ q: query.trim(), team })
      .then(data => {
        if (requestId.current !== token) return;
        setPlayers(data.players);
        setSeason(data.season);
        setTeams(data.teams);
        setTotal(data.total);
        remember(data.cursor);
      })
      .catch(err => {
        if (requestId.current !== token) return;
        setError(err instanceof Error ? err.message : 'Could not load players.');
      })
      .finally(() => { if (requestId.current === token) setLoading(false); });
    return () => { requestId.current += 1; };
  }, [query, team]);

  const loadMore = useCallback(async () => {
    const after = cursorRef.current;
    if (after == null || loadingMoreRef.current) return;
    const token = requestId.current;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const data = await fetchPlayerLibrary({ cursor: after, q: queryRef.current.trim(), team: teamRef.current });
      if (requestId.current !== token) return;
      setPlayers(prev => {
        const seen = new Set(prev.map(player => player.id));
        return [...prev, ...data.players.filter(player => !seen.has(player.id))];
      });
      setTotal(data.total);
      remember(data.cursor);
    } catch (err) {
      if (requestId.current !== token) return;
      setError(err instanceof Error ? err.message : 'Could not load players.');
    } finally {
      if (requestId.current === token) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, []);

  return {
    players,
    season,
    teams,
    total,
    query,
    setQuery,
    team,
    setTeam,
    loading,
    loadingMore,
    hasMore: cursor !== null,
    error,
    loadMore,
  };
}
