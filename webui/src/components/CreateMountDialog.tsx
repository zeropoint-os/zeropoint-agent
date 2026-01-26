import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, QueueEnqueueCreateMountRequest, ApiDisk } from 'artifacts/clients/typescript';
import './CreateMountDialog.css';

interface Props {
  onClose: () => void;
  onMountCreated?: (jobId?: string) => void;
}

interface PartitionOption {
  kname: string;
  path: string;
  sizeBytes?: number;
  type?: string;
}

export default function CreateMountDialog({ onClose, onMountCreated }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const [mountPoint, setMountPoint] = useState('');
  const [filesystem, setFilesystem] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [partitions, setPartitions] = useState<PartitionOption[]>([]);

  useEffect(() => {
    fetchDisks();
  }, []);

  const fetchDisks = async () => {
    try {
      const resp = await storageApi.apiStorageDisksGet();
      setDisks(resp || []);
      buildPartitionList(resp || []);
    } catch (err) {
      console.error('Failed to load disks', err);
    }
  };

  const buildPartitionList = (diskList: ApiDisk[]) => {
    const parts: PartitionOption[] = [];
    diskList.forEach((disk) => {
      if (disk.partitions && disk.partitions.length > 0) {
        disk.partitions.forEach((p) => {
          parts.push({
            kname: p.kname || '',
            path: p.sysPath || '',
            sizeBytes: p.sizeBytes,
            type: p.fsType || 'ext4',
          });
        });
      }
    });
    setPartitions(parts);
  };

  const handleFilesystemChange = (kname: string) => {
    setFilesystem(kname);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!mountPoint.trim()) {
      setError('Mount point is required');
      return;
    }
    if (!filesystem.trim()) {
      setError('Filesystem is required');
      return;
    }

    setLoading(true);
    try {
      // Extract filesystem type from the selected partition
      const partition = partitions.find((p) => p.kname === filesystem);
      const fsType = partition?.type || 'ext4';

      const req: QueueEnqueueCreateMountRequest = {
        mountPoint: mountPoint,
        filesystem: filesystem,
        type: fsType,
      };
      const response = await jobsApi.enqueueCreateMount({ queueEnqueueCreateMountRequest: req });
      if (onMountCreated) {
        onMountCreated(response.id);
      }
      onClose();
    } catch (err) {
      console.error('Failed to create mount', err);
      setError(err instanceof Error ? err.message : 'Failed to create mount');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Create Mount</h2>
          <button
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleCreate} className="modal-form">
          {error && (
            <div className="error-message">
              <p>{error}</p>
            </div>
          )}

          <div className="form-group">
            <label htmlFor="mountPoint">Mount Point *</label>
            <input
              id="mountPoint"
              type="text"
              placeholder="/mnt/storage"
              value={mountPoint}
              onChange={(e) => setMountPoint(e.target.value)}
              disabled={loading}
            />
            <small>The directory where the filesystem will be mounted (e.g., /mnt/storage)</small>
          </div>

          <div className="form-group">
            <label htmlFor="filesystem">Filesystem *</label>
            <select
              id="filesystem"
              value={filesystem}
              onChange={(e) => setFilesystem(e.target.value)}
              disabled={loading}
            >
              <option value="">Select a partition...</option>
              {partitions.map((p) => (
                <option key={p.kname} value={p.kname}>
                  {p.kname}, {p.type || 'ext4'}
                </option>
              ))}
            </select>
            <small>Select a disk partition to mount</small>
          </div>

          <div className="modal-actions">
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
              disabled={loading || !mountPoint.trim() || !filesystem.trim()}
            >
              {loading ? 'Creating...' : 'Create Mount'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
