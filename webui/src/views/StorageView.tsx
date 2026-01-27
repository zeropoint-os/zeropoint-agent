import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { StorageApi, Configuration, ApiDisk, ApiMount, ApiPath, JobsApi, QueueEnqueueFormatRequest } from 'artifacts/clients/typescript';
import FormatView from './FormatView';
import CreateMountDialog from '../components/CreateMountDialog';
import EditPathDialog from '../components/EditPathDialog';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function StorageView() {
  const navigate = useNavigate();
  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [mounts, setMounts] = useState<ApiMount[]>([]);
  const [paths, setPaths] = useState<ApiPath[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSections, setExpandedSections] = useState<{ [k: string]: boolean }>({ disks: true, mounts: true, paths: false });
  const [showEditPathDialog, setShowEditPathDialog] = useState(false);
  const [selectedPath, setSelectedPath] = useState<ApiPath | null>(null);
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

  const fetchMounts = async () => {
    try {
      const resp = await storageApi.apiStorageMountsGet();
      setMounts(resp?.mounts || []);
    } catch (err) {
      console.error('Failed to load mounts', err);
      setMounts([]);
    }
  };

  const fetchPaths = async () => {
    try {
      const resp = await storageApi.apiStoragePathsGet();
      setPaths(resp?.paths || []);
    } catch (err) {
      console.error('Failed to load paths', err);
      setPaths([]);
    }
  };

  const jobsApi = new JobsApi(new Configuration({ basePath: '' }));

  const [formatTarget, setFormatTarget] = useState<ApiDisk | null>(null);
  const [formatting, setFormatting] = useState(false);
  const [showCreateMountDialog, setShowCreateMountDialog] = useState(false);

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
    fetchMounts();
    fetchPaths();
    const interval = setInterval(() => {
      fetchDisks();
      fetchMounts();
      fetchPaths();
    }, 5000);
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

  const getDiskNameById = (diskId?: string): string => {
    if (!diskId) return 'Unknown';
    const disk = disks.find(d => d.id === diskId);
    if (!disk) return diskId;
    // Prefer model name, fall back to vendor + size
    if (disk.model && disk.model.trim()) {
      return disk.model.trim();
    }
    if (disk.vendor) {
      const size = formatBytes(disk.sizeBytes);
      return `${disk.vendor.trim()} ${size}`;
    }
    return diskId;
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
          <div className="section-content">
            {mounts.length === 0 ? (
              <div className="empty-state">
                <h3>No mounts configured</h3>
                <p>Add a mount to configure additional filesystems.</p>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {mounts.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      backgroundColor: 'var(--color-surface)',
                      border: `1px solid var(--color-border)`,
                      borderRadius: '0.5rem',
                      padding: '1rem',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        filesystem: {m.id} ({m.mountPoint})
                      </div>
                      <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        Mounted on: {getDiskNameById(m.diskId)} • {m.type}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      <span
                        style={{
                          padding: '0.25rem 0.75rem',
                          borderRadius: '0.25rem',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          backgroundColor: m.status === 'active' ? 'rgba(34, 197, 94, 0.1)' : 'rgba(251, 146, 60, 0.1)',
                          color: m.status === 'active' ? '#22c55e' : '#fb923c',
                        }}
                      >
                        {m.status}
                      </span>
                      {m.mountPoint !== '/' && (
                        <button
                          className="button button-danger"
                          style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                          onClick={() => {
                            // TODO: Handle mount deletion
                            alert('Delete mount not yet implemented in UI');
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: '1rem' }}>
              <button className="button button-primary" onClick={() => setShowCreateMountDialog(true)}>
                Add Mount
              </button>
            </div>
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
          <div className="section-content">
            {paths.length === 0 ? (
              <div className="empty-state">
                <h3>No paths configured</h3>
                <p>Add a path to configure storage locations.</p>
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                {paths.map((p) => (
                  <div
                    key={p.id}
                    style={{
                      backgroundColor: 'var(--color-surface)',
                      border: `1px solid var(--color-border)`,
                      borderRadius: '0.5rem',
                      padding: '1rem',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        {p.isSystemPath ? 'SYSTEM PATH' : 'USER PATH'}: {p.id}
                      </div>
                      <div style={{ fontWeight: 700, marginBottom: '0.5rem' }}>{p.path}</div>
                      {p.description && (
                        <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>
                          {p.description}
                        </div>
                      )}
                      <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        Mount: {p.mountId} • Status: {p.status}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      <button
                        className="button button-secondary"
                        style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                        onClick={() => {
                          setSelectedPath(p);
                          setShowEditPathDialog(true);
                        }}
                      >
                        Edit
                      </button>
                      {!p.isSystemPath && (
                        <button
                          className="button button-danger"
                          style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                          onClick={() => {
                            if (confirm(`Delete path ${p.id}?`)) {
                              jobsApi.enqueueDeleteUserPath({
                                queueEnqueueDeleteUserPathRequest: { pathId: p.id },
                              }).then(() => {
                                fetchPaths();
                              }).catch((err) => {
                                alert('Failed to delete path: ' + (err instanceof Error ? err.message : String(err)));
                              });
                            }
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: '1rem' }}>
              <button
                className="button button-primary"
                onClick={() => {
                  setSelectedPath(null);
                  setShowEditPathDialog(true);
                }}
              >
                Add Path
              </button>
            </div>
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

      {/* Create mount modal */}
      {showCreateMountDialog && (
        <CreateMountDialog
          onClose={() => setShowCreateMountDialog(false)}
          onMountCreated={(jobId?: string) => {
            fetchMounts();
            setShowCreateMountDialog(false);
            if (jobId) {
              navigate('/jobs');
            }
          }}
        />
      )}

      {/* Edit path modal */}
      {showEditPathDialog && (
        <EditPathDialog
          path={selectedPath || undefined}
          onClose={() => {
            setShowEditPathDialog(false);
            setSelectedPath(null);
          }}
          onSaved={() => {
            fetchPaths();
            setShowEditPathDialog(false);
            setSelectedPath(null);
          }}
        />
      )}
    </div>
  );
}
