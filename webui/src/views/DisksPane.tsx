import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { StorageApi, Configuration, ApiDisk, JobsApi, QueueEnqueueFormatRequest } from 'artifacts/clients/typescript';
import FormatView from './FormatView';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function DisksPane() {
  const navigate = useNavigate();
  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [formatTarget, setFormatTarget] = useState<ApiDisk | null>(null);
  const loadingTimeout = useRef<NodeJS.Timeout | null>(null);

  // StorageApi paths include /api prefix in generated client, use empty basePath to avoid double /api
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const fetchDisks = async () => {
    loadingTimeout.current = setTimeout(() => setLoading(true), LOADING_INDICATOR_DELAY);
    try {
      // Get managed disks (from disks.ini)
      const managedResp = await storageApi.apiStorageDisksGet();
      const managedDisks = managedResp || [];

      // Get discovered disks (all available)
      const discoveredResp = await storageApi.apiStorageDisksDiscoverGet();
      const discoveredMap = new Map<string, ApiDisk>();
      (discoveredResp || []).forEach((d: ApiDisk) => {
        if (d.id) {
          discoveredMap.set(d.id, d);
        }
      });

      // Enrich managed disks with discover info
      const enriched = managedDisks.map((managed) => {
        const discovered = managed.id ? discoveredMap.get(managed.id) : undefined;
        return discovered || managed;
      });

      setDisks(enriched);
      setError(null);
    } catch (err: unknown) {
      console.error('Failed to load disks', err);
      setError(err instanceof Error ? err.message : 'Failed to load disks');
      setDisks([]);
    } finally {
      if (loadingTimeout.current) {
        clearTimeout(loadingTimeout.current);
      }
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDisks();
    const interval = setInterval(fetchDisks, 5000);
    return () => clearInterval(interval);
  }, []);

  const formatBytes = (n?: number) => {
    if (!n || n <= 0) return '-';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) {
      v = v / 1024;
      i++;
    }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
  };

  const handleRelease = async (diskId?: string) => {
    if (!diskId) return;
    try {
      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      await jobsApi.enqueueReleaseDisk({ queueEnqueueReleaseDiskRequest: { id: diskId } });
      // Refetch disks after releasing
      await fetchDisks();
    } catch (err: unknown) {
      console.error('Failed to release disk', err);
      setError(err instanceof Error ? err.message : 'Failed to release disk');
    }
  };

  return (
    <div className="section-block">
      <div
        role="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="section-header"
      >
        <h2 className="section-title">Disks</h2>
        <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', borderRadius: 6, background: 'var(--color-surface-alt)' }}>
          {expanded ? '▼' : '▶'}
        </div>
      </div>

      {expanded && (
        <div className="section-content">
          {error && (
            <div className="error-state">
              <p className="error-message">{error}</p>
              <button className="button button-secondary" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          )}

          {loading && disks.length === 0 ? (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading disks...</p>
            </div>
          ) : disks.length === 0 ? (
            <div className="empty-state">
              <h3>No disks found</h3>
              <p>Attach a disk and refresh.</p>
              <button className="button button-secondary" onClick={fetchDisks}>
                Refresh
              </button>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
              {disks.map((d) => (
                <div
                  key={d.id}
                  style={{
                    backgroundColor: 'var(--color-surface)',
                    border: `1px solid var(--color-border)`,
                    borderRadius: '0.5rem',
                    padding: '1.5rem',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    flexDirection: 'column',
                    height: '100%',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--color-surface-hover)';
                    (e.currentTarget as HTMLElement).style.boxShadow = '0 6px 18px rgba(0,0,0,0.25)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--color-surface)';
                    (e.currentTarget as HTMLElement).style.boxShadow = 'none';
                  }}
                >
                  {/* Warning if disk not found in discover */}
                  {!d.vendor && (
                    <div
                      style={{
                        backgroundColor: 'rgba(239, 68, 68, 0.1)',
                        border: '1px solid rgba(239, 68, 68, 0.5)',
                        borderRadius: '0.4rem',
                        padding: '0.75rem',
                        marginBottom: '0.75rem',
                        fontSize: '0.8rem',
                        color: '#ef4444',
                      }}
                    >
                      ⚠️ Disk not detected - it may have been disconnected or is offline
                    </div>
                  )}

                  {/* Header: Vendor, Size, Transport Tags - only if we have discovery info */}
                  {d.vendor ? (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem', gap: '1rem' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--color-text)' }}>
                          {d.vendor}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: '0.25rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                          {d.transport && (
                            <span
                              style={{
                                padding: '0.2rem 0.5rem',
                                borderRadius: '0.2rem',
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                backgroundColor: d.transport === 'usb' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                                color: d.transport === 'usb' ? '#22c55e' : '#3b82f6',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {d.transport}
                            </span>
                          )}
                          {d.boot && (
                            <span
                              style={{
                                padding: '0.2rem 0.5rem',
                                borderRadius: '0.2rem',
                                fontSize: '0.7rem',
                                fontWeight: 600,
                                backgroundColor: 'rgba(251, 146, 60, 0.15)',
                                color: '#fb923c',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              boot
                            </span>
                          )}
                        </div>
                      </div>
                      <div style={{ textAlign: 'right', flexShrink: 0 }}>
                        <div style={{ fontSize: '1rem', fontWeight: 700, whiteSpace: 'nowrap' }}>{formatBytes(d.sizeBytes)}</div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--color-text-secondary)', marginTop: '0.25rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '150px' }}>
                          {d.sysPath || d.serial || d.id}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ marginBottom: '1rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--color-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {d.id}
                      </div>
                    </div>
                  )}

                  {/* Model/Name - only if available */}
                  {d.model && (
                    <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', marginBottom: '0.75rem' }}>
                      {d.model}
                    </div>
                  )}

                  {/* Partitions Section - only if we have discovery info */}
                  {d.vendor && (
                    <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem', marginBottom: '1rem' }}>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--color-text-secondary)' }}>
                        Partitions
                      </div>
                      {(!d.partitions || d.partitions.length === 0) ? (
                        <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>No partitions</div>
                      ) : (
                        <div style={{ display: 'grid', gap: '0.5rem' }}>
                          {d.partitions.map((p) => (
                            <div key={p.kname} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div>
                                <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text)' }}>{p.kname}</div>
                                <div style={{ fontSize: '0.7rem', color: 'var(--color-text-secondary)' }}>{p.sysPath}</div>
                              </div>
                              <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--color-text)' }}>
                                {formatBytes(p.sizeBytes)}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Actions */}
                  <div style={{ marginTop: 'auto', display: 'flex', gap: '0.5rem' }}>
                    {!d.boot && (
                      <button
                        className="button button-danger"
                        onClick={() => handleRelease(d.id)}
                        style={{ flex: 1 }}
                      >
                        Release
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Format modal */}
      {formatTarget && (
        <FormatView
          disk={formatTarget}
          onClose={() => setFormatTarget(null)}
          onEnqueued={() => {
            fetchDisks();
            setFormatTarget(null);
            navigate('/jobs');
          }}
        />
      )}
    </div>
  );
}
