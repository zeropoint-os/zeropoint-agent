import React, { useEffect, useState, useRef } from 'react';
import { StorageApi, Configuration, ApiMount, ApiDisk, JobsApi } from 'artifacts/clients/typescript';
import CreateLocalDiskMountDialog from './CreateLocalDiskMountDialog';
import EditMountDialog from './EditMountDialog';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function MountsPane() {
  const [mounts, setMounts] = useState<ApiMount[]>([]);
  const [disksMap, setDisksMap] = useState<Map<string, ApiDisk>>(new Map());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [selectedMountForEdit, setSelectedMountForEdit] = useState<ApiMount | null>(null);
  const loadingTimeout = useRef<NodeJS.Timeout | null>(null);

  const storageApi = new StorageApi(new Configuration({ basePath: '' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  const fetchMounts = async () => {
    loadingTimeout.current = setTimeout(() => setLoading(true), LOADING_INDICATOR_DELAY);
    try {
      const resp = await storageApi.apiStorageMountsGet();
      setMounts(resp?.mounts || []);
      
      // Also fetch discovered disks to enrich mount display
      const discoveredResp = await storageApi.apiStorageDisksDiscoverGet();
      const diskMap = new Map<string, ApiDisk>();
      (discoveredResp || []).forEach((d: ApiDisk) => {
        if (d.id) {
          diskMap.set(d.id, d);
        }
      });
      setDisksMap(diskMap);
      
      setError(null);
    } catch (err: unknown) {
      console.error('Failed to load mounts', err);
      setError(err instanceof Error ? err.message : 'Failed to load mounts');
      setMounts([]);
    } finally {
      if (loadingTimeout.current) {
        clearTimeout(loadingTimeout.current);
      }
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMounts();
    const interval = setInterval(fetchMounts, 5000);
    return () => clearInterval(interval);
  }, []);

  const getStatusColor = (status?: string) => {
    switch (status) {
      case 'active':
        return '#22c55e';
      case 'pending':
        return '#f59e0b';
      default:
        return 'var(--color-text-secondary)';
    }
  };

  const handleCreateSuccess = () => {
    setShowCreateDialog(false);
    fetchMounts();
  };

  const handleDeleteMount = async (mount: ApiMount) => {
    if (!window.confirm(`Are you sure you want to delete mount ${mount.mountPoint}?`)) {
      return;
    }

    try {
      await jobsApi.enqueueDeleteMount({
        queueEnqueueDeleteMountRequest: {
          mountPoint: mount.mountPoint || '',
        },
      });
      fetchMounts();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to delete mount');
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
        <h2 className="section-title">Mounts</h2>
        <div style={{ width: 36, height: 36, display: 'grid', placeItems: 'center', borderRadius: 6, background: 'var(--color-surface-alt)' }}>
          {expanded ? '▼' : '▶'}
        </div>
      </div>

      {expanded && (
        <div className="section-content">
          {error && (
            <div style={{ padding: '12px', backgroundColor: 'var(--color-error-bg)', color: 'var(--color-error)', borderRadius: 4, marginBottom: 12 }}>
              {error}
            </div>
          )}

          {loading && mounts.length === 0 ? (
            <div className="empty-state">
              <div className="spinner"></div>
              <p>Loading mounts...</p>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
              {/* Mount Cards */}
              {mounts.map((mount) => {
                const disk = mount.disk ? disksMap.get(mount.disk) : undefined;
                const partition = disk && mount.partition !== null && mount.partition !== undefined 
                  ? disk.partitions?.[mount.partition] 
                  : undefined;
                
                return (
                  <div
                    key={mount.id}
                    style={{
                      padding: '1.5rem',
                      backgroundColor: 'var(--color-surface)',
                      borderRadius: '8px',
                      border: '1px solid var(--color-border)',
                      display: 'flex',
                      flexDirection: 'column',
                      transition: 'all var(--transition-normal)',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.22)';
                      e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.04)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.boxShadow = '';
                      e.currentTarget.style.borderColor = 'var(--color-border)';
                    }}
                  >
                    <div style={{ marginBottom: '0.75rem' }}>
                      <div style={{ fontSize: '0.95rem', fontWeight: 600, color: 'var(--color-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {mount.mountPoint || '(unknown)'}
                      </div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: '0.5rem' }}>
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '0.25rem 0.75rem',
                            borderRadius: '0.2rem',
                            fontSize: '0.7rem',
                            fontWeight: 600,
                            backgroundColor: getStatusColor(mount.status) === '#22c55e' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                            color: getStatusColor(mount.status),
                          }}
                        >
                          {mount.status || 'unknown'}
                        </span>
                      </div>
                    </div>

                    <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem', marginBottom: '1rem' }}>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--color-text-secondary)' }}>
                        Details
                      </div>
                      <div style={{ display: 'grid', gap: '0.5rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-secondary)' }}>Disk</div>
                          <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--color-text)' }}>
                            {disk?.model || disk?.vendor || 'unknown'}
                          </div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-secondary)' }}>Partition</div>
                          <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--color-text)' }}>
                            {partition?.kname || `Partition ${mount.partition}`}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Action Buttons */}
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: 'auto' }}>
                      <button
                        onClick={() => setSelectedMountForEdit(mount)}
                        style={{
                          flex: 1,
                          padding: '0.5rem',
                          fontSize: '0.8rem',
                          backgroundColor: 'var(--color-primary)',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontWeight: 500,
                          transition: 'background-color var(--transition-normal)',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = 'var(--color-primary-hover)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = 'var(--color-primary)';
                        }}
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDeleteMount(mount)}
                        style={{
                          flex: 1,
                          padding: '0.5rem',
                          fontSize: '0.8rem',
                          backgroundColor: '#dc2626',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontWeight: 500,
                          transition: 'background-color var(--transition-normal)',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = '#b91c1c';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = '#dc2626';
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                );
              })}

              {/* Add Mount Card - Last Item */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minHeight: '200px',
                }}
              >
                <button
                  className="button button-primary"
                  onClick={() => setShowCreateDialog(true)}
                >
                  + Mount
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {showCreateDialog && (
        <CreateLocalDiskMountDialog
          onClose={() => setShowCreateDialog(false)}
          onSuccess={handleCreateSuccess}
        />
      )}

      {selectedMountForEdit && (
        <EditMountDialog
          mount={selectedMountForEdit}
          onClose={() => setSelectedMountForEdit(null)}
          onSuccess={handleCreateSuccess}
        />
      )}
    </div>
  );
}
