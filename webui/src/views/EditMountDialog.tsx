import React, { useState, useEffect } from 'react';
import { JobsApi, StorageApi, Configuration, ApiDisk, ApiMount, QueueEnqueueEditMountRequest } from 'artifacts/clients/typescript';
import './CreateLocalDiskMountDialog.css';

interface Props {
  mount: ApiMount;
  onClose: () => void;
  onSuccess?: () => void;
}

export default function EditMountDialog({ mount, onClose, onSuccess }: Props) {
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
  const storageApi = new StorageApi(new Configuration({ basePath: '' }));

  const [disks, setDisks] = useState<ApiDisk[]>([]);
  const [selectedDisk, setSelectedDisk] = useState<ApiDisk | null>(null);
  const [selectedPartition, setSelectedPartition] = useState<number | null>(null);
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

      // Pre-select current mount's disk and partition
      if (mount.disk) {
        const currentDisk = enriched.find((d) => d.id === mount.disk);
        setSelectedDisk(currentDisk || null);
      }
      if (mount.partition !== null && mount.partition !== undefined) {
        setSelectedPartition(mount.partition);
      }
    } catch (err: unknown) {
      console.error('Failed to load disks', err);
      setError(err instanceof Error ? err.message : 'Failed to load disks');
    } finally {
      setDisksLoading(false);
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
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

    setLoading(true);
    try {
      const req: QueueEnqueueEditMountRequest = {
        disk: selectedDisk.id,
        partition: selectedPartition,
        mountPoint: mount.mountPoint || '',
      };

      await jobsApi.enqueueEditMount({ queueEnqueueEditMountRequest: req });
      if (onSuccess) onSuccess();
      onClose();
    } catch (err: unknown) {
      console.error('Failed to enqueue mount edit', err);
      setError(err instanceof Error ? err.message : 'Failed to edit mount');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal">
        <div className="modal-header">
          <h2>Edit Mount: {mount.mountPoint}</h2>
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

          <form onSubmit={handleUpdate}>
            {/* Disk Selector */}
            <div className="form-group">
              <label className="form-label">Managed Disk *</label>
              <select
                value={selectedDisk?.id || ''}
                onChange={(e) => {
                  const diskId = e.target.value;
                  const disk = disks.find((d) => d.id === diskId);
                  setSelectedDisk(disk || null);
                  setSelectedPartition(null);
                }}
                disabled={disksLoading || loading}
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
                    : 'Unknown';
                  return (
                    <option key={disk.id} value={disk.id || ''}>
                      {disk.model || disk.vendor || disk.id} - {sizeStr}
                    </option>
                  );
                })}
              </select>
            </div>

            {/* Partition Selector */}
            <div className="form-group">
              <label className="form-label">Partition *</label>
              <select
                value={selectedPartition ?? ''}
                onChange={(e) => setSelectedPartition(parseInt(e.target.value))}
                disabled={!selectedDisk || loading}
                className="form-input"
              >
                <option value="">Select a partition</option>
                {selectedDisk?.partitions?.map((partition, idx) => (
                  <option key={idx} value={idx}>
                    {partition.kname || `Partition ${idx}`} ({partition.sizeBytes} bytes)
                  </option>
                ))}
              </select>
            </div>

            {/* Action Buttons */}
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
              <button
                type="submit"
                disabled={loading || !selectedDisk || selectedPartition === null}
                className="button button-primary"
                style={{ flex: 1 }}
              >
                {loading ? 'Updating...' : 'Update'}
              </button>
              <button
                type="button"
                onClick={onClose}
                disabled={loading}
                className="button button-secondary"
                style={{ flex: 1 }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
