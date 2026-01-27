import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, ApiPath, ApiMount } from 'artifacts/clients/typescript';
import './EditPathDialog.css';

interface Props {
  path?: ApiPath; // If provided, edit existing path; if not, create new
  onClose: () => void;
  onSaved?: (pathId?: string) => void;
}

export default function EditPathDialog({ path, onClose, onSaved }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const [name, setName] = useState('');
  const [pathValue, setPathValue] = useState('');
  const [mountId, setMountId] = useState('');
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mounts, setMounts] = useState<ApiMount[]>([]);
  const isSystemPath = path?.isSystemPath || false;
  const isEditing = !!path;

  useEffect(() => {
    if (path) {
      setName(path.name || '');
      setPathValue(path.path || '');
      setMountId(path.mountId || '');
      setDescription(path.description || '');
    }
    fetchMounts();
  }, [path]);

  const fetchMounts = async () => {
    try {
      const resp = await storageApi.apiStorageMountsGet();
      setMounts(resp?.mounts || []);
    } catch (err) {
      console.error('Failed to load mounts', err);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (!pathValue.trim()) {
      setError('Path is required');
      return;
    }
    if (!mountId.trim()) {
      setError('Mount is required');
      return;
    }

    setLoading(true);
    try {
      if (isEditing && path?.id) {
        // Edit existing path - system or user
        await jobsApi.enqueueEditPath({
          queueEnqueueEditPathRequest: {
            id: path.id,
            name,
            path: pathValue,
            mountId,
            description,
          },
        });
        alert(isSystemPath ? 'Path edit job enqueued - will be applied at boot' : 'Path updated successfully');
      } else {
        // Create new user path
        if (!path?.id) {
          setError('Path ID is required for new paths');
          setLoading(false);
          return;
        }
        await jobsApi.enqueueAddPath({
          queueEnqueueAddPathRequest: {
            id: path.id,
            name,
            path: pathValue,
            mountId,
            description,
          },
        });
        alert('Path created successfully');
      }
      onSaved?.(path?.id);
      onClose();
    } catch (err) {
      console.error('Failed to save path', err);
      setError(err instanceof Error ? err.message : 'Failed to save path');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!isEditing || !path?.id) return;
    if (isSystemPath) {
      setError('Cannot delete system paths');
      return;
    }

    if (!confirm('Delete this path?')) return;

    setLoading(true);
    try {
      await jobsApi.enqueueDeletePath({
        queueEnqueueDeletePathRequest: {
          id: path.id,
        },
      });
      alert('Path deleted successfully');
      onSaved?.(path.id);
      onClose();
    } catch (err) {
      console.error('Failed to delete path', err);
      setError(err instanceof Error ? err.message : 'Failed to delete path');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog-content" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>{isEditing ? `Edit Path: ${path?.id}` : 'Add Path'}</h2>
          <button className="dialog-close" onClick={onClose}>×</button>
        </div>

        {isSystemPath && (
          <div style={{ backgroundColor: 'rgba(251, 146, 60, 0.1)', border: '1px solid rgba(251, 146, 60, 0.3)', borderRadius: '0.5rem', padding: '0.75rem', marginBottom: '1rem', fontSize: '0.85rem', color: '#fb923c' }}>
            System paths (zp_* prefix) are managed by the system and trigger data migration at boot.
          </div>
        )}

        {error && (
          <div style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.3)', borderRadius: '0.5rem', padding: '0.75rem', marginBottom: '1rem', fontSize: '0.85rem', color: '#ef4444' }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSave}>
          <div className="form-group">
            <label htmlFor="name">Name</label>
            <input
              id="name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isSystemPath}
              placeholder="e.g., Docker Storage"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="path">Path</label>
            <input
              id="path"
              type="text"
              value={pathValue}
              onChange={(e) => setPathValue(e.target.value)}
              placeholder="e.g., /var/lib/docker"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="mountId">Mount</label>
            <select
              id="mountId"
              value={mountId}
              onChange={(e) => setMountId(e.target.value)}
              disabled={isSystemPath}
              required
            >
              <option value="">Select a mount...</option>
              {mounts.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id} ({m.mountPoint})
                </option>
              ))}
            </select>
          </div>

          {!isSystemPath && (
            <div className="form-group">
              <label htmlFor="description">Description (optional)</label>
              <textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Add a description..."
                rows={3}
              />
            </div>
          )}

          <div className="dialog-actions">
            {isEditing && !isSystemPath && (
              <button
                type="button"
                className="button button-danger"
                onClick={handleDelete}
                disabled={loading}
              >
                Delete
              </button>
            )}
            <div style={{ flex: 1 }} />
            <button
              type="button"
              className="button button-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button button-primary"
              disabled={loading}
            >
              {loading ? 'Saving...' : isEditing ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
