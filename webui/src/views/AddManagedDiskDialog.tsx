import React, { useState, useMemo } from 'react';
import { StorageApi, Configuration, ApiDisk, JobsApi } from 'artifacts/clients/typescript';
import './Dialog.css';

interface AddManagedDiskDialogProps {
  managedDisks: ApiDisk[];
  discoveredDisks: ApiDisk[];
  onClose: () => void;
  onSuccess: () => void;
}

export default function AddManagedDiskDialog({
  managedDisks,
  discoveredDisks,
  onClose,
  onSuccess,
}: AddManagedDiskDialogProps) {
  const [selectedDiskId, setSelectedDiskId] = useState<string>('');
  const [wipefs, setWipefs] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filter to show only unmanaged disks
  const availableDisks = useMemo(() => {
    const managedIds = new Set(managedDisks.map((d) => d.id));
    return discoveredDisks.filter((d) => d.id && !managedIds.has(d.id));
  }, [managedDisks, discoveredDisks]);

  const selectedDisk = availableDisks.find((d) => d.id === selectedDiskId);

  const formatDiskLabel = (disk: ApiDisk) => {
    const parts = [];
    if (disk.vendor) parts.push(disk.vendor);
    if (disk.model) parts.push(disk.model);
    const name = parts.length > 0 ? parts.join(' ') : disk.id || 'Unknown';
    const size = disk.sizeBytes ? formatBytes(disk.sizeBytes) : 'Unknown size';
    const transport = disk.transport ? ` (${disk.transport})` : '';
    return `${name} - ${size}${transport}`;
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 10) / 10 + ' ' + sizes[i];
  };

  const handleAdd = async () => {
    if (!selectedDiskId) return;

    setLoading(true);
    setError(null);

    try {
      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      
      const args: Record<string, any> = {
        id: selectedDiskId,
        auto_partition: wipefs,
        wipefs: wipefs,
      };
      
      // If formatting (wipefs), require explicit confirmation
      if (wipefs) {
        args.confirm = true;
        args.filesystem = 'ext4';
        args.label = 'managed';
      }

      await jobsApi.enqueueManageDisk({
        queueEnqueueManageDiskRequest: args,
      });

      onSuccess();
      onClose();
    } catch (err: unknown) {
      console.error('Failed to manage disk', err);
      setError(err instanceof Error ? err.message : 'Failed to manage disk');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>Add Managed Disk</h2>
          <button className="dialog-close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="dialog-content">
          {error && (
            <div className="form-error">
              <p>{error}</p>
            </div>
          )}

          {availableDisks.length === 0 ? (
            <p>No available disks to manage. All discovered disks are already managed or no disks are available.</p>
          ) : (
            <>
              <div className="form-group">
                <label htmlFor="disk-select">Select Disk</label>
                <select
                  id="disk-select"
                  value={selectedDiskId}
                  onChange={(e) => setSelectedDiskId(e.target.value)}
                >
                  <option value="">-- Select a disk --</option>
                  {availableDisks.map((disk) => (
                    <option key={disk.id} value={disk.id}>
                      {formatDiskLabel(disk)}
                    </option>
                  ))}
                </select>
              </div>

              {selectedDisk && (
                <div style={{ padding: '1rem', backgroundColor: 'var(--color-surface-alt)', borderRadius: 'var(--border-radius)' }}>
                  <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>Disk Details</div>
                  {selectedDisk.sysPath && (
                    <div style={{ fontSize: '0.9rem' }}>
                      <strong>Path:</strong> {selectedDisk.sysPath}
                    </div>
                  )}
                  {selectedDisk.partitions && selectedDisk.partitions.length > 0 && (
                    <div style={{ fontSize: '0.9rem' }}>
                      <strong>Partitions:</strong> {selectedDisk.partitions.length}
                    </div>
                  )}
                </div>
              )}

              <div className="form-group">
                <label htmlFor="wipefs-check" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <input
                    id="wipefs-check"
                    type="checkbox"
                    checked={wipefs}
                    onChange={(e) => setWipefs(e.target.checked)}
                  />
                  <span>Format disk (erase and partition)</span>
                </label>
              </div>

              {wipefs ? (
                <div className="form-error">
                  ⚠️ <strong>Warning:</strong> This will wipe your disk of all partitions, erase any data and make it usable by Zeropoint.
                </div>
              ) : (
                <div className="form-info">
                  ✓ This will not erase any data. You can pick a folder to use later.
                </div>
              )}
            </>
          )}
        </div>

        <div className="dialog-footer">
          <button className="button button-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          {availableDisks.length > 0 && (
            <button
              className="button button-primary"
              onClick={handleAdd}
              disabled={!selectedDiskId || loading}
            >
              {loading ? 'Adding...' : 'Add Disk'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
