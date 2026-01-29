import React, { useEffect, useState, useRef } from 'react';
import { StorageApi, Configuration, ApiMountPath } from 'artifacts/clients/typescript';
import CreatePathDialog from './CreatePathDialog';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function PathsPane() {
  const [paths, setPaths] = useState<ApiMountPath[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const loadingTimeout = useRef<NodeJS.Timeout | null>(null);

  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const fetchPaths = async () => {
    loadingTimeout.current = setTimeout(() => setLoading(true), LOADING_INDICATOR_DELAY);
    try {
      const resp = await (storageApi as any).apiStoragePathsGet();
      setPaths(resp?.paths || []);
      setError(null);
    } catch (err: unknown) {
      console.error('Failed to load paths', err);
      setError(err instanceof Error ? err.message : 'Failed to load paths');
      setPaths([]);
    } finally {
      if (loadingTimeout.current) {
        clearTimeout(loadingTimeout.current);
      }
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPaths();
    const interval = setInterval(fetchPaths, 5000);
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
    fetchPaths();
  };

  return (
    <div className="section-block">
      <div
        role="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="section-header"
      >
        <h2 className="section-title">Paths</h2>
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

          {loading && paths.length === 0 ? (
            <div className="empty-state">
              <div className="spinner"></div>
              <p>Loading paths...</p>
            </div>
          ) : paths.length === 0 ? (
            <div className="empty-state">
              <h3>No paths configured</h3>
              <p>Create a path to add a subdirectory to a mount.</p>
              <button className="button button-secondary" onClick={() => setShowCreateDialog(true)}>
                Add Path
              </button>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1rem' }}>
              {/* Path Cards */}
              {paths
                .filter((path) => path.id && !path.id.startsWith('!'))
                .map((path) => (
                <div
                  key={path.id}
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
                      {path.fullPath || `${path.mount}/${path.pathSuffix}`}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginTop: '0.5rem' }}>
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '0.25rem 0.75rem',
                          borderRadius: '0.2rem',
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          backgroundColor: getStatusColor(path.status) === '#22c55e' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                          color: getStatusColor(path.status),
                        }}
                      >
                        {path.status || 'unknown'}
                      </span>
                    </div>
                  </div>

                  <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: '0.75rem' }}>
                    <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--color-text-secondary)' }}>
                      Details
                    </div>
                    <div style={{ display: 'grid', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-secondary)' }}>Mount</div>
                        <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--color-text)' }}>
                          {path.mount}
                        </div>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--color-text-secondary)' }}>Suffix</div>
                        <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--color-text)' }}>
                          {path.pathSuffix}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}

              {/* Add Path Card - Last Item */}
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
                  + Add Path
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {showCreateDialog && (
        <CreatePathDialog
          onClose={() => setShowCreateDialog(false)}
          onSuccess={handleCreateSuccess}
        />
      )}
    </div>
  );
}
