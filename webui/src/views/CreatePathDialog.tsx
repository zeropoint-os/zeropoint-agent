import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, ApiMount } from 'artifacts/clients/typescript';
import './CreateLocalDiskMountDialog.css';

interface Props {
  onClose: () => void;
  onSuccess?: () => void;
}

export default function CreatePathDialog({ onClose, onSuccess }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const [mounts, setMounts] = useState<ApiMount[]>([]);
  const [selectedMount, setSelectedMount] = useState<ApiMount | null>(null);
  const [pathSuffix, setPathSuffix] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mountsLoading, setMountsLoading] = useState(false);

  useEffect(() => {
    fetchMounts();
  }, []);

  const fetchMounts = async () => {
    setMountsLoading(true);
    try {
      const resp = await storageApi.apiStorageMountsGet();
      setMounts(resp?.mounts || []);
    } catch (err: unknown) {
      console.error('Failed to load mounts', err);
      setError(err instanceof Error ? err.message : 'Failed to load mounts');
    } finally {
      setMountsLoading(false);
    }
  };

  const validatePathSuffix = (suffix: string): string | null => {
    if (!suffix) return 'Path suffix is required';
    if (suffix.startsWith('/')) return 'Path suffix cannot start with /';
    if (suffix === '.' || suffix === '..') return 'Path suffix cannot be . or ..';
    if (/\/\.\.($|\/)/.test(suffix)) return 'Path suffix cannot contain .. traversal';
    if (!/^[a-zA-Z0-9_\-/]+$/.test(suffix)) return 'Path suffix contains invalid characters';
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate
    if (!selectedMount || !selectedMount.id) {
      setError('Please select a mount');
      return;
    }

    const suffixError = validatePathSuffix(pathSuffix);
    if (suffixError) {
      setError(suffixError);
      return;
    }

    setLoading(true);
    try {
      await jobsApi.enqueueCreatePathMount({ queueEnqueueCreateMountPathRequest: { mount: selectedMount.id, pathSuffix: pathSuffix } });
      if (onSuccess) onSuccess();
      onClose();
    } catch (err: unknown) {
      console.error('Failed to enqueue path creation', err);
      setError(err instanceof Error ? err.message : 'Failed to create path');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal">
        <div className="modal-header">
          <h2>Add Path</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-form">
          {error && (
            <div className="form-error">
              <p>{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            {/* Mount Selector */}
            <div className="form-group">
              <label className="form-label">Mount *</label>
              <select
                value={selectedMount?.id || ''}
                onChange={(e) => {
                  const mountId = e.target.value;
                  const mount = mounts.find((m) => m.id === mountId);
                  setSelectedMount(mount || null);
                }}
                disabled={mountsLoading}
                className="form-input"
              >
                <option value="">
                  {mountsLoading ? 'Loading mounts...' : 'Select a mount'}
                </option>
                {mounts
                  .filter((mount) => mount.status === 'active')
                  .map((mount) => (
                    <option key={mount.id} value={mount.id || ''}>
                      {mount.mountPoint || 'Unknown'} ({mount.id})
                    </option>
                  ))}
              </select>
              <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 4 }}>
                Only active mounts are available
              </div>
            </div>

            {/* Path Suffix Input */}
            <div className="form-group">
              <label className="form-label">Path Suffix *</label>
              <input
                type="text"
                value={pathSuffix}
                onChange={(e) => setPathSuffix(e.target.value)}
                placeholder="media"
                className="form-input"
                disabled={loading}
              />
              <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 4 }}>
                Subdirectory name within the mount (e.g., media, photos, backup)
              </div>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                onClick={onClose}
                disabled={loading}
                className="button"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading || mountsLoading}
                className="button button-primary"
              >
                {loading ? 'Creating...' : 'Add Path'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
