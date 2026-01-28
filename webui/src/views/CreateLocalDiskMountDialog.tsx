import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, ApiDisk, QueueEnqueueCreateMountRequest } from 'artifacts/clients/typescript';
import './CreateLocalDiskMountDialog.css';

interface Props {
  onClose: () => void;
  onSuccess?: () => void;
}

export default function CreateLocalDiskMountDialog({ onClose, onSuccess }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [selectedDisk, setSelectedDisk] = useState<ApiDisk | null>(null);
  const [selectedPartition, setSelectedPartition] = useState<number | null>(null);
  const [mountPoint, setMountPoint] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disksLoading, setDisksLoading] = useState(false);

  useEffect(() => {
    fetchManagedDisks();
  }, []);

  const fetchManagedDisks = async () => {
    setDisksLoading(true);
    try {
      // Get managed disks (from disks.ini)
      const managedResp = await storageApi.apiStorageDisksGet();
      const managedDisks = managedResp || [];

      // Get discovered disks (all available) to enrich with partition data
      const discoveredResp = await storageApi.apiStorageDisksDiscoverGet();
      const discoveredMap = new Map<string, ApiDisk>();
      (discoveredResp || []).forEach((d: ApiDisk) => {
        if (d.id) {
          discoveredMap.set(d.id, d);
        }
      });

      // Enrich managed disks with discover info (especially partitions)
      const enriched = managedDisks.map((managed) => {
        const discovered = managed.id ? discoveredMap.get(managed.id) : undefined;
        return discovered || managed;
      });

      setDisks(enriched);
    } catch (err: unknown) {
      console.error('Failed to load disks', err);
      setError(err instanceof Error ? err.message : 'Failed to load disks');
    } finally {
      setDisksLoading(false);
    }
  };

  const validateMountPoint = (path: string): string | null => {
    if (!path) return 'Mount point is required';
    if (!path.startsWith('/')) return 'Mount point must start with /';
    if (path === '/') return 'Cannot mount at root (/)';
    if (/[^a-zA-Z0-9/_-]/.test(path)) return 'Mount point contains invalid characters';
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate
    if (!selectedDisk || !selectedDisk.id) {
      setError('Please select a disk');
      return;
    }
    if (selectedPartition === null) {
      setError('Please select a partition');
      return;
    }

    const mountPointError = validateMountPoint(mountPoint);
    if (mountPointError) {
      setError(mountPointError);
      return;
    }

    setLoading(true);
    try {
      const req: QueueEnqueueCreateMountRequest = {
        disk: selectedDisk.id,
        partition: selectedPartition,
        mountPoint: mountPoint,
      };

      await jobsApi.enqueueCreateMount({ queueEnqueueCreateMountRequest: req });
      if (onSuccess) onSuccess();
      onClose();
    } catch (err: unknown) {
      console.error('Failed to enqueue mount', err);
      setError(err instanceof Error ? err.message : 'Failed to create mount');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal">
        <div className="modal-header">
          <h2>Create Mount</h2>
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
            {/* Disk Selector */}
            <div className="form-group">
              <label className="form-label">Managed Disk *</label>
              <select
                value={selectedDisk?.id || ''}
                onChange={(e) => {
                  const diskId = e.target.value;
                  const disk = disks.find((d) => d.id === diskId);
                  setSelectedDisk(disk || null);
                  setSelectedPartition(null); // Reset partition when disk changes
                }}
                disabled={disksLoading}
                className="form-input"
              >
                <option value="">
                  {disksLoading ? 'Loading disks...' : 'Select a disk'}
                </option>
                {disks.map((disk) => {
                  const sizeStr = disk.sizeBytes
                    ? (() => {
                        let size = disk.sizeBytes;
                        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
                        let unitIdx = 0;
                        while (size >= 1024 && unitIdx < units.length - 1) {
                          size = size / 1024;
                          unitIdx++;
                        }
                        return `${size.toFixed(1)} ${units[unitIdx]}`;
                      })()
                    : '-';
                  return (
                    <option key={disk.id} value={disk.id || ''}>
                      {disk.model || 'Unknown'} - {sizeStr} ({disk.id})
                    </option>
                  );
                })}
              </select>
            </div>

            {/* Partition Selector */}
            {selectedDisk && selectedDisk.partitions && selectedDisk.partitions.length > 0 && (
              <div className="form-group">
                <label className="form-label">Partition *</label>
                <select
                  value={selectedPartition ?? ''}
                  onChange={(e) => setSelectedPartition(parseInt(e.target.value, 10))}
                  className="form-input"
                >
                  <option value="">Select a partition</option>
                  {selectedDisk.partitions.map((partition, idx) => {
                    const sizeStr = partition.sizeBytes
                      ? (() => {
                          let size = partition.sizeBytes;
                          const units = ['B', 'KB', 'MB', 'GB', 'TB'];
                          let unitIdx = 0;
                          while (size >= 1024 && unitIdx < units.length - 1) {
                            size = size / 1024;
                            unitIdx++;
                          }
                          return `${size.toFixed(1)} ${units[unitIdx]}`;
                        })()
                      : '-';
                    return (
                      <option key={idx} value={idx}>
                        Partition {partition.index || idx + 1} - {sizeStr}
                        {partition.fsType ? ` (${partition.fsType})` : ''}
                      </option>
                    );
                  })}
                </select>
              </div>
            )}

            {selectedDisk && (!selectedDisk.partitions || selectedDisk.partitions.length === 0) && (
              <div className="form-group">
                <div style={{ padding: 12, backgroundColor: 'var(--color-warning-bg)', color: 'var(--color-warning)', borderRadius: 4 }}>
                  No partitions found on this disk
                </div>
              </div>
            )}

            {/* Mount Point Input */}
            <div className="form-group">
              <label className="form-label">Mount Point *</label>
              <input
                type="text"
                value={mountPoint}
                onChange={(e) => setMountPoint(e.target.value)}
                placeholder="/mnt/storage"
                className="form-input"
                disabled={loading}
              />
              <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 4 }}>
                Where to mount the filesystem (e.g., /mnt/storage)
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
                disabled={loading || disksLoading}
                className="button button-primary"
              >
                {loading ? 'Creating...' : 'Create Mount'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
