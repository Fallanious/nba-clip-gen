import { useEffect, useState } from 'react';
import { fetchPlayerLibrary } from '../api';
import type { BrowserPlayer } from '../types';

export function usePlayerLibrary() {
  const [players, setPlayers] = useState<BrowserPlayer[]>([]);
  const [season, setSeason] = useState('');
  const [query, setQuery] = useState('');
  const [team, setTeam] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let stop = false;
    fetchPlayerLibrary()
      .then(data => {
        if (stop) return;
        setPlayers(data.players);
        setSeason(data.season);
        setError('');
      })
      .catch(err => {
        if (!stop) setError(err instanceof Error ? err.message : 'Could not load players.');
      })
      .finally(() => { if (!stop) setLoading(false); });
    return () => { stop = true; };
  }, []);

  const teams = [...new Set(players.map(player => player.team).filter(Boolean))].sort();
  const shown = players.filter(player => {
    const name = player.full_name.toLowerCase().includes(query.trim().toLowerCase());
    return name && (!team || player.team === team);
  });

  return { players, season, query, setQuery, team, setTeam, teams, shown, loading, error };
}
