import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { StorageApi, Configuration, ApiDisk, JobsApi, QueueEnqueueFormatRequest } from 'artifacts/clients/typescript';
import FormatView from './FormatView';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function StorageView() {
  const navigate = useNavigate();
  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSections, setExpandedSections] = useState<{ [k: string]: boolean }>({ disks: true, mounts: false, paths: false });
  const loadingTimeout = useRef<NodeJS.Timeout | null>(null);

  // StorageApi paths include /api prefix in generated client, use empty basePath to avoid double /api
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const fetchDisks = async () => {
    loadingTimeout.current = setTimeout(() => setLoading(true), LOADING_INDICATOR_DELAY);
    try {
      const resp = await storageApi.apiStorageDisksGet();
      setDisks(resp || []);
      setError(null);
    } catch (err) {
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

  const jobsApi = new JobsApi(new Configuration({ basePath: '' }));

  const [formatTarget, setFormatTarget] = useState<ApiDisk | null>(null);
  const [formatting, setFormatting] = useState(false);

  const confirmFormat = async (disk: ApiDisk) => {
    setFormatTarget(disk);
  };

  const doEnqueueFormat = async () => {
    if (!formatTarget) return;
    setFormatting(true);
    try {
      const req: QueueEnqueueFormatRequest = {
        id: formatTarget.id,
        confirm: true,
        autoPartition: true,
        confirmFixedDiskOperation: (formatTarget.transport || '').toLowerCase() !== 'usb',
        wipefs: true,
      };
      await jobsApi.enqueueFormat({ queueEnqueueFormatRequest: req });
      // simple feedback — refresh disks and close modal
      fetchDisks();
      setFormatTarget(null);
      alert('Format job enqueued');
    } catch (err) {
      console.error('Failed to enqueue format', err);
      alert('Failed to enqueue format: ' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setFormatting(false);
    }
  };

  useEffect(() => {
    fetchDisks();
    const interval = setInterval(fetchDisks, 5000);
    return () => clearInterval(interval);
  }, []);

  const toggleSection = (key: string) => {
    setExpandedSections((s) => ({ ...s, [key]: !s[key] }));
  };

  const formatBytes = (n?: number) => {
    if (!n || n <= 0) return '-';
    const units = ['B','KB','MB','GB','TB'];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length-1) { v = v / 1024; i++; }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Storage</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="button button-secondary" onClick={fetchDisks}>Refresh</button>
        </div>
      </div>

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {/* Disks section */}
      <div className="section-block">
        <div
          role="button"
          aria-expanded={expandedSections.disks}
          onClick={() => toggleSection('disks')}
          className="section-header"
        >
          <h2 className="section-title">Disks</h2>
          <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', borderRadius: 6, background: 'var(--color-surface-alt)' }}>{expandedSections.disks ? '▼' : '▶'}</div>
        </div>

        {expandedSections.disks && (
          <div className="section-content">
            {loading && disks.length === 0 ? (
              <div className="loading-state">
                <div className="spinner"></div>
                <p>Loading disks...</p>
              </div>
            ) : disks.length === 0 ? (
              <div className="empty-state">
                <h3>No disks found</h3>
                <p>Attach a disk and refresh.</p>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '1rem' }}>
                {disks.map((d) => (
                  <div
                    key={d.id || d.id}
                    style={{
                      backgroundColor: 'var(--color-surface)',
                      border: `1px solid var(--color-border)`,
                      borderRadius: '0.5rem',
                      padding: '1.5rem',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                      borderLeft: `4px solid var(--color-border)`,
                      display: 'flex',
                      flexDirection: 'column',
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
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                      <div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 700 }}>{d.model || d.id || d.id}</div>
                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginTop: '0.25rem' }}>
                          <div style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{d.vendor || ''}</div>
                          {/* boot tag (backend best-effort) - show first and prominent */}
                          {d.boot ? (
                            <div className="tag tag-boot" title="Boot disk">boot</div>
                          ) : null}
                          {/* transport tag */}
                          {d.transport && (
                            <div className={`tag ${d.transport === 'usb' ? 'tag-transport-usb' : 'tag-transport-nonusb'}`}>
                              {d.transport}
                            </div>
                          )}
                        </div>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontWeight: 700 }}>{formatBytes(d.sizeBytes)}</div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)' }}>{d.sysPath || d.serial || d.id}</div>
                      </div>
                    </div>

                    <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem' }}>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>Partitions</div>
                      {(!d.partitions || d.partitions.length === 0) && (
                        <div style={{ color: 'var(--color-text-secondary)' }}>No partitions</div>
                      )}
                      {d.partitions && d.partitions.map((p) => (
                        <div key={p.partitionId || p.kname} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.25rem 0' }}>
                          <div>
                            <div style={{ fontWeight: 600 }}>{p.kname}</div>
                            <div style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>{p.sysPath}</div>
                          </div>
                          <div style={{ textAlign: 'right', fontSize: '0.85rem' }}>{formatBytes(p.sizeBytes)}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 'auto', display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                      {!d.boot && (
                        <button className="button button-danger" onClick={() => confirmFormat(d)}>Format</button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Mounts section */}
      <div className="section-block">
        <div
          role="button"
          aria-expanded={expandedSections.mounts}
          onClick={() => toggleSection('mounts')}
          className="section-header"
        >
          <h2 className="section-title">Mounts</h2>
          <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', borderRadius: 6, background: 'var(--color-surface-alt)' }}>{expandedSections.mounts ? '▼' : '▶'}</div>
        </div>
        {expandedSections.mounts && (
          <div className="section-content" style={{ minHeight: '120px', border: '1px dashed var(--color-border)', borderRadius: '0.5rem', padding: '1rem', color: 'var(--color-text-secondary)' }}>
            Mounts UI will go here.
          </div>
        )}
      </div>

      {/* Paths section */}
      <div className="section-block">
        <div
          role="button"
          aria-expanded={expandedSections.paths}
          onClick={() => toggleSection('paths')}
          className="section-header"
        >
          <h2 className="section-title">Paths</h2>
          <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', borderRadius: 6, background: 'var(--color-surface-alt)' }}>{expandedSections.paths ? '▼' : '▶'}</div>
        </div>
        {expandedSections.paths && (
          <div className="section-content" style={{ minHeight: '120px', border: '1px dashed var(--color-border)', borderRadius: '0.5rem', padding: '1rem', color: 'var(--color-text-secondary)' }}>
            Paths UI will go here.
          </div>
        )}
      </div>

      {/* Format modal */}
      {formatTarget && (
        <FormatView
          disk={formatTarget}
          onClose={() => setFormatTarget(null)}
          onEnqueued={(jobId?: string) => {
            fetchDisks();
            setFormatTarget(null);
            navigate('/jobs');
          }}
        />
      )}
    </div>
  );
}
