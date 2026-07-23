import { useEffect, useState, useCallback } from 'react';
import { fetchOllamaStatus } from '../api';

export default function StatusBar() {
  const [online, setOnline] = useState(false);
  const [text, setText] = useState('Checking Ollama...');

  const check = useCallback(async () => {
    try {
      const data = await fetchOllamaStatus();
      if (data.status === 'online') {
        setOnline(true);
        setText(`Ollama online (${data.models.length} models)`);
      } else {
        setOnline(false);
        setText('Ollama offline');
      }
    } catch {
      setOnline(false);
      setText('Cannot reach server');
    }
  }, []);

  useEffect(() => {
    check();
    const id = setInterval(check, 30000);
    return () => clearInterval(id);
  }, [check]);

  return (
    <div className="status-bar">
      <div className="status-indicator">
        <div className={`status-dot ${online ? 'online' : ''}`} />
        <span>{text}</span>
      </div>
      <button className="btn-small" onClick={check}>Refresh</button>
    </div>
  );
}
