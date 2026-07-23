import { useState, useEffect, useCallback } from 'react';
import { fetchVideos, fetchPlayByPlay, fetchTimestamps } from './api';
import type { Video, PlayByPlayFile, TimestampFile } from './types';

import StatusBar from './components/StatusBar';
import DownloadVideo from './components/DownloadVideo';
import ScrapePlayByPlay from './components/ScrapePlayByPlay';
import MatchTimestamps from './components/MatchTimestamps';
import EditLabels from './components/EditLabels';
import GenerateClips from './components/GenerateClips';

export default function App() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [pbpFiles, setPbpFiles] = useState<PlayByPlayFile[]>([]);
  const [timestampFiles, setTimestampFiles] = useState<TimestampFile[]>([]);

  const loadVideos = useCallback(async () => {
    try {
      setVideos(await fetchVideos());
    } catch (err) {
      console.error('Failed to load videos:', err);
    }
  }, []);

  const loadPBP = useCallback(async () => {
    try {
      setPbpFiles(await fetchPlayByPlay());
    } catch (err) {
      console.error('Failed to load PBP:', err);
    }
  }, []);

  const loadTimestamps = useCallback(async () => {
    try {
      setTimestampFiles(await fetchTimestamps());
    } catch (err) {
      console.error('Failed to load timestamps:', err);
    }
  }, []);

  useEffect(() => {
    loadVideos();
    loadPBP();
    loadTimestamps();
  }, [loadVideos, loadPBP, loadTimestamps]);

  return (
    <div className="container">
      <h1>NBA Clip Generator</h1>

      <StatusBar />

      <DownloadVideo onComplete={loadVideos} />

      <ScrapePlayByPlay videos={videos} onComplete={loadPBP} />

      <MatchTimestamps
        videos={videos}
        pbpFiles={pbpFiles}
        onRefreshVideos={loadVideos}
        onRefreshPBP={loadPBP}
        onComplete={loadTimestamps}
      />

      <EditLabels
        timestampFiles={timestampFiles}
        onRefreshTimestamps={loadTimestamps}
      />

      <GenerateClips
        videos={videos}
        timestampFiles={timestampFiles}
        onRefreshTimestamps={loadTimestamps}
      />
    </div>
  );
}
