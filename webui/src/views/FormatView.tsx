import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, ApiDisk, QueueEnqueueFormatRequest } from 'artifacts/clients/typescript';

interface Props {
  disk: ApiDisk;
  onClose: () => void;
  onEnqueued?: (jobId?: string) => void;
}

type UnmountStatus = 'idle' | 'unmounting' | 'ready' | 'error';

export default function FormatView({ disk, onClose, onEnqueued }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));
  
  const [dryRun, setDryRun] = useState(true);
  const [autoPartition] = useState(true); // forced for now
  const [filesystem, setFilesystem] = useState('ext4');
  const [label, setLabel] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const [unmountStatus, setUnmountStatus] = useState<UnmountStatus>('idle');
  const [unmountLogs, setUnmountLogs] = useState<string[]>([]);

  // Unmount on mount
  useEffect(() => {
    unmountDiskOnOpen();
  }, []);

  const unmountDiskOnOpen = async () => {
    setUnmountStatus('unmounting');
    setUnmountLogs([]);
    try {
      const response = await fetch(`/api/storage/disks/${disk.id}/unmount`, {
        method: 'POST',
      });

      if (!response.body) {
        setUnmountStatus('error');
        setError('No response from unmount endpoint');
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const message = line.slice(6);
            setUnmountLogs((prev) => [...prev, message]);
          }
        }
      }

      setUnmountStatus('ready');
    } catch (err) {
      setUnmountStatus('error');
      setError('Failed to unmount: ' + (err instanceof Error ? err.message : String(err)));
      setUnmountLogs((prev) => [...prev, 'Error: ' + (err instanceof Error ? err.message : String(err))]);
    }
  };

  const enqueue = async () => {
    setError(null);
    setLoading(true);
    try {
      const req: QueueEnqueueFormatRequest = {
        id: disk.id,
        confirm: true,
        autoPartition: autoPartition,
        filesystem: filesystem,
        label: label || undefined,
        wipefs: !dryRun,
        confirmFixedDiskOperation: (disk.transport || '').toLowerCase() !== 'usb',
      };

      const resp = await jobsApi.enqueueFormat({ queueEnqueueFormatRequest: req });
      const jobId = (resp && (resp as any).id) || undefined;
      if (onEnqueued) onEnqueued(jobId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal">
        <div className="modal-header">
          <h2>Format {disk.model || disk.id}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-form">
          {error && <div className="form-error"><p>{error}</p></div>}

          {/* Unmount status section */}
          <div className="form-group">
            <label className="form-label">
              Unmount Status:{' '}
              {unmountStatus === 'unmounting' && '⏳ Unmounting...'}
              {unmountStatus === 'ready' && '✓ Ready'}
              {unmountStatus === 'error' && '✗ Error'}
              {unmountStatus === 'idle' && 'Checking...'}
            </label>
            {unmountLogs.length > 0 && (
              <div
                style={{
                  backgroundColor: 'var(--color-surface-alt)',
                  borderRadius: '0.25rem',
                  padding: '0.5rem',
                  fontSize: '0.8rem',
                  color: 'var(--color-text-secondary)',
                  maxHeight: '120px',
                  overflowY: 'auto',
                  fontFamily: 'monospace',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {unmountLogs.map((log, i) => (
                  <div key={i}>{log}</div>
                ))}
              </div>
            )}
          </div>

          {unmountStatus === 'ready' && (
            <>
              <div className="form-group">
                <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run (simulate wipefs and partitioning without destructive writes)
                </label>
              </div>

              <div className="form-group">
                <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <input type="checkbox" checked={autoPartition} disabled /> Auto partition (creates single disk-wide GPT partition)
                </label>
              </div>

              <div className="form-group">
                <label className="form-label">Filesystem</label>
                <select value={filesystem} onChange={(e) => setFilesystem(e.target.value)} className="form-input">
                  <option value="ext4">ext4 (recommended)</option>
                  <option value="xfs">xfs</option>
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">Volume Label (optional)</label>
                <input className="form-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g., DATA" />
              </div>
            </>
          )}

          <div className="modal-footer">
            <button className="button" onClick={onClose} disabled={loading || unmountStatus === 'unmounting'}>Cancel</button>
            <button 
              className="button button-danger" 
              onClick={enqueue} 
              disabled={loading || unmountStatus !== 'ready'}
            >
              {loading ? 'Enqueueing...' : unmountStatus === 'ready' ? 'Enqueue Format' : 'Waiting for unmount...'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
