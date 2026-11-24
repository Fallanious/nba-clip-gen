import { useState, useEffect } from 'react';
import { fetchPlays } from '../api';
import type { Play, TimestampFile } from '../types';
import Section from './Section';
import OutputArea from './OutputArea';
import VideoPreview from './VideoPreview';

interface Props {
  selectedFile: string;
  chooseFile: (path: string) => void;
  timestampFiles: TimestampFile[];
  onRefreshTimestamps: () => void;
}

export default function EditLabels({ selectedFile, chooseFile, timestampFiles, onRefreshTimestamps }: Props) {
  const [plays, setPlays] = useState<Play[]>([]);
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState<number>(-1);
  const [error, setError] = useState('');

  useEffect(() => {
    let current = true;
    if (selectedFile) {
      fetchPlays(selectedFile).then(data => {
        if (!current) return;
        setPlays(data.plays);
        setVideoPath(data.video);
      }).catch(err => {
        if (current) setError(err instanceof Error ? err.message : 'Failed to load plays');
      });
    }
    return () => { current = false; };
  }, [selectedFile]);

  const labelCounts: Record<string, number> = {};
  plays.forEach(p => {
    const l = p.primary_action || 'other';
    labelCounts[l] = (labelCounts[l] || 0) + 1;
  });
  const countsStr = Object.entries(labelCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([l, c]) => `${l}: ${c}`)
    .join(', ');

  const formatTime = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <Section title="3. Review Plays">
      <div className="form-group">
        <label>Timestamps File</label>
        <div className="form-row">
          <select value={selectedFile} onChange={e => chooseFile(e.target.value)}>
            <option value="">-- Select Timestamps --</option>
            {timestampFiles.map(f => (
              <option key={f.path} value={f.path}>
                {f.name} ({f.play_count} plays)
              </option>
            ))}
          </select>
          <button className="btn-small" onClick={onRefreshTimestamps}>Refresh</button>
        </div>
      </div>

      {previewIndex >= 0 && videoPath && plays[previewIndex] && (
        <VideoPreview
          play={plays[previewIndex]}
          videoPath={videoPath ?? ""}
          onClose={() => setPreviewIndex(-1)}
        />
      )}

      {plays.length > 0 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <span style={{ fontSize: 13, color: '#aaa' }}>
              {plays.length} plays &mdash; {countsStr}
            </span>
          </div>
          <div style={{ maxHeight: 500, overflowY: 'auto', borderRadius: 6 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#1f3460', position: 'sticky', top: 0, zIndex: 1 }}>
                  <th style={{ padding: '8px 10px', textAlign: 'center', width: 40 }}></th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', width: 30 }}>#</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', width: 70 }}>Team</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', width: 60 }}>Qtr</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', width: 70 }}>Time</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left' }}>Description</th>
                  <th style={{ padding: '8px 10px', textAlign: 'left', width: 140 }}>Primary</th>
                </tr>
              </thead>
              <tbody className="label-table-body">
                {plays.map((play, i) => {
                  const desc = play.description || '';
                  const truncDesc = desc.length > 60 ? desc.substring(0, 57) + '...' : desc;
                  const hasVideo = !!videoPath && play.video_start != null;
                  const isPreviewing = previewIndex === i;

                  return (
                    <tr
                      key={i}
                      className={isPreviewing ? 'previewing' : ''}
                    >
                      <td style={{ textAlign: 'center' }}>
                        {hasVideo && (
                          <button
                            className={`btn-preview ${isPreviewing ? 'active' : ''}`}
                            onClick={() => setPreviewIndex(i)}
                            title={`Preview ${formatTime(play.video_start)} - ${formatTime(play.video_end)}`}
                          >
                            &#9654;
                          </button>
                        )}
                      </td>
                      <td style={{ color: '#666' }}>{i}</td>
                      <td>{play.team || ''}</td>
                      <td>Q{play.quarter || ''}</td>
                      <td>{play.game_time || ''}</td>
                      <td className="desc-text" title={desc}>{truncDesc}</td>
                      <td>{play.primary_action || 'other'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {error && <OutputArea text={error} status="failed" />}
    </Section>
  );
}
