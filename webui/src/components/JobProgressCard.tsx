import React from 'react';
import type { QueueJobResponse } from 'artifacts/clients/typescript';
import './InstallationProgress.css';

type OperationType = 'install' | 'uninstall' | 'create_link' | 'create_exposure' | 'delete_link' | 'delete_exposure';

interface JobProgressCardProps {
  job: QueueJobResponse | null;
  itemName?: string;
  onCancel?: () => void;
  operationType?: OperationType;
}

function getActionLabel(operationType: OperationType): string {
  const labels: Record<OperationType, string> = {
    'install': 'Installing',
    'uninstall': 'Uninstalling',
    'create_link': 'Creating Link',
    'create_exposure': 'Creating Exposure',
    'delete_link': 'Deleting Link',
    'delete_exposure': 'Deleting Exposure',
  };
  return labels[operationType];
}

function getProgressLabel(operationType: OperationType): string {
  const labels: Record<OperationType, string> = {
    'install': 'Installing',
    'uninstall': 'Uninstalling',
    'create_link': 'Creating Link',
    'create_exposure': 'Creating Exposure',
    'delete_link': 'Deleting Link',
    'delete_exposure': 'Deleting Exposure',
  };
  return labels[operationType];
}

export default function JobProgressCard({ job, itemName, onCancel, operationType = 'install' }: JobProgressCardProps) {
  const actionLabel = getActionLabel(operationType);
  const displayName = itemName ? ` ${itemName}` : '';
  
  if (!job) {
    return (
      <div className="installation-progress">
        <div className="progress-header">
          <div className="spinner" style={{ width: '20px', height: '20px', borderWidth: '2px' }}></div>
          <span className="progress-title">{actionLabel}{displayName}...</span>
        </div>
      </div>
    );
  }

  const statusClass = `status-${job.status?.toLowerCase() || 'unknown'}`;
  const isQueued = job.status === 'queued';
  const isRunning = job.status === 'running';
  const isCompleted = job.status === 'completed';
  const isFailed = job.status === 'failed';
  const isCancelled = job.status === 'cancelled';

  const displayStatus = () => {
    if (isQueued) return 'Queued';
    if (isRunning) return getProgressLabel(operationType);
    if (isCompleted) return 'Completed';
    if (isFailed) return 'Failed';
    if (isCancelled) return 'Cancelled';
    return job.status || 'Unknown';
  };

  return (
    <div className={`installation-progress ${statusClass}`}>
      <div className="progress-header">
        {(isQueued || isRunning) && (
          <div className="spinner" style={{ width: '20px', height: '20px', borderWidth: '2px' }}></div>
        )}
        {isCompleted && (
          <div className="status-icon success">✓</div>
        )}
        {isFailed && (
          <div className="status-icon error">✕</div>
        )}
        {isCancelled && (
          <div className="status-icon cancelled">⊘</div>
        )}
        <span className="progress-title">{displayStatus()}</span>
        {isQueued && onCancel && (
          <button className="button button-small button-danger" onClick={onCancel} style={{ marginLeft: 'auto' }}>
            Cancel
          </button>
        )}
      </div>

      {isFailed && job.error && (
        <div className="progress-error">
          <p className="error-text">{job.error}</p>
        </div>
      )}

      {job.events && job.events.length > 0 && (
        <div className="progress-events">
          <div className="events-label">Progress:</div>
          <div className="events-list">
            {job.events.map((event, idx) => (
              <div key={idx} className="event-item">
                <span className="event-timestamp">{new Date(event.timestamp || '').toLocaleTimeString()}</span>
                <span className="event-message">{event.message || event.type || ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
