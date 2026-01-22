import React, { useState, useEffect, useRef } from 'react';
import { JobsApi, Configuration, QueueJobResponse } from 'artifacts/clients/typescript';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

export default function JobsView() {
  const [jobs, setJobs] = useState<QueueJobResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'active' | 'completed' | 'failed'>('all');
  const loadingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  // Fetch all jobs
  const fetchJobs = async () => {
    loadingTimeoutRef.current = setTimeout(() => {
      setLoading(true);
    }, LOADING_INDICATOR_DELAY);

    try {
      const response = await jobsApi.listJobs({});
      const jobList = response.jobs ?? [];
      setJobs(jobList);
      setError(null);
    } catch (err) {
      console.error('Error loading jobs:', err);
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
      setJobs([]);
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
      }
      setLoading(false);
    }
  };

  // Initial fetch and polling
  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 2000);
    return () => clearInterval(interval);
  }, []);

  // Filter jobs based on selected filter
  const filteredJobs = jobs.filter(job => {
    if (filter === 'all') return true;
    if (filter === 'active') return job.status === 'queued' || job.status === 'running';
    if (filter === 'completed') return job.status === 'completed';
    if (filter === 'failed') return job.status === 'failed' || job.status === 'cancelled';
    return true;
  });

  // Sort by creation time (most recent first)
  const sortedJobs = [...filteredJobs].sort((a, b) => {
    const aTime = a.createdAt ? new Date(a.createdAt).getTime() : 0;
    const bTime = b.createdAt ? new Date(b.createdAt).getTime() : 0;
    return bTime - aTime;
  });

  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'completed': return 'var(--color-success)';
      case 'running': return 'var(--color-primary)';
      case 'queued': return 'var(--color-warning)';
      case 'failed': return 'var(--color-danger)';
      case 'cancelled': return 'var(--color-text-secondary)';
      default: return 'var(--color-text-secondary)';
    }
  };

  const formatTime = (dateString?: string): string => {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleTimeString();
  };

  const formatDate = (dateString?: string): string => {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleDateString();
  };

  const getDuration = (startTime?: string, endTime?: string): string => {
    if (!startTime) return '-';
    const start = new Date(startTime).getTime();
    const end = endTime ? new Date(endTime).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  };

  return (
    <div className="view-container">
      {jobs.length > 0 && (
        <div className="view-header">
          <h1 className="section-title">Jobs</h1>
          <button
            className="button button-secondary"
            onClick={() => fetchJobs()}
          >
            Refresh
          </button>
        </div>
      )}

      {jobs.length === 0 && (
        <h1 className="section-title">Jobs</h1>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {loading && jobs.length === 0 ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading jobs...</p>
        </div>
      ) : (
        <>
          <div style={{ marginBottom: '1.5rem', display: 'flex', gap: '0.5rem' }}>
            <button
              className={`button ${filter === 'all' ? 'button-primary' : 'button-secondary'}`}
              onClick={() => setFilter('all')}
            >
              All ({jobs.length})
            </button>
            <button
              className={`button ${filter === 'active' ? 'button-primary' : 'button-secondary'}`}
              onClick={() => setFilter('active')}
            >
              Active ({jobs.filter(j => j.status === 'queued' || j.status === 'running').length})
            </button>
            <button
              className={`button ${filter === 'completed' ? 'button-primary' : 'button-secondary'}`}
              onClick={() => setFilter('completed')}
            >
              Completed ({jobs.filter(j => j.status === 'completed').length})
            </button>
            <button
              className={`button ${filter === 'failed' ? 'button-primary' : 'button-secondary'}`}
              onClick={() => setFilter('failed')}
            >
              Failed ({jobs.filter(j => j.status === 'failed' || j.status === 'cancelled').length})
            </button>
          </div>

          {sortedJobs.length === 0 ? (
            <div className="empty-state">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2"></path>
              </svg>
              <h2>No jobs found</h2>
              <p>Jobs will appear here as you perform actions.</p>
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: `2px solid var(--color-border)`, backgroundColor: 'var(--color-surface-alt)' }}>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>ID</th>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>Type</th>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>Status</th>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>Created</th>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>Duration</th>
                    <th style={{ padding: '1rem', textAlign: 'left', fontWeight: '600' }}>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedJobs.map((job) => (
                    <React.Fragment key={job.id}>
                      <tr
                        style={{
                          borderBottom: `1px solid var(--color-border)`,
                          backgroundColor: expandedJobId === job.id ? 'var(--color-surface-hover)' : 'var(--color-surface)',
                          cursor: 'pointer',
                        }}
                        onClick={() =>
                          setExpandedJobId(expandedJobId === job.id ? null : job.id ?? null)
                        }
                      >
                        <td style={{ padding: '1rem', fontFamily: 'monospace', fontSize: '0.875rem' }}>
                          {job.id?.substring(0, 8)}...
                        </td>
                        <td style={{ padding: '1rem' }}>
                          <span style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                            {job.command?.type || '-'}
                          </span>
                        </td>
                        <td style={{ padding: '1rem' }}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '0.25rem 0.75rem',
                              borderRadius: '0.375rem',
                              backgroundColor: `color-mix(in srgb, ${getStatusColor(job.status || '')} 15%, transparent)`,
                              color: getStatusColor(job.status || ''),
                              fontSize: '0.875rem',
                              fontWeight: '500',
                            }}
                          >
                            {job.status || '-'}
                          </span>
                        </td>
                        <td style={{ padding: '1rem', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                          {formatDate(job.createdAt)} {formatTime(job.createdAt)}
                        </td>
                        <td style={{ padding: '1rem', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                          {getDuration(job.startedAt, job.completedAt)}
                        </td>
                        <td style={{ padding: '1rem', textAlign: 'center' }}>
                          <span style={{ fontSize: '0.875rem', color: 'var(--color-primary)' }}>
                            {expandedJobId === job.id ? '▼' : '▶'}
                          </span>
                        </td>
                      </tr>

                      {/* Expanded details */}
                      {expandedJobId === job.id && (
                        <tr style={{ backgroundColor: 'var(--color-surface-alt)' }}>
                          <td colSpan={6} style={{ padding: '1.5rem' }}>
                            <div style={{ display: 'grid', gap: '1rem' }}>
                              {/* Command details */}
                              <div>
                                <h4 style={{ marginBottom: '0.5rem', fontWeight: '600' }}>Command</h4>
                                <div
                                  style={{
                                    backgroundColor: 'var(--color-surface)',
                                    padding: '1rem',
                                    borderRadius: '0.375rem',
                                    fontFamily: 'monospace',
                                    fontSize: '0.875rem',
                                    overflowX: 'auto',
                                    border: `1px solid var(--color-border)`,
                                  }}
                                >
                                  <pre style={{ margin: 0 }}>
                                    {JSON.stringify(job.command, null, 2)}
                                  </pre>
                                </div>
                              </div>

                              {/* Dependencies */}
                              {job.dependsOn && job.dependsOn.length > 0 && (
                                <div>
                                  <h4 style={{ marginBottom: '0.5rem', fontWeight: '600' }}>
                                    Depends On ({job.dependsOn.length})
                                  </h4>
                                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                                    {job.dependsOn.map((depId) => (
                                      <span
                                        key={depId}
                                        style={{
                                          backgroundColor: 'var(--color-border)',
                                          padding: '0.25rem 0.75rem',
                                          borderRadius: '0.375rem',
                                          fontSize: '0.875rem',
                                          fontFamily: 'monospace',
                                        }}
                                      >
                                        {depId.substring(0, 8)}...
                                      </span>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Error message */}
                              {job.error && (
                                <div>
                                  <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', color: 'var(--color-danger)' }}>
                                    Error
                                  </h4>
                                  <div
                                    style={{
                                      backgroundColor: 'var(--color-danger-light)',
                                      color: 'var(--color-danger)',
                                      padding: '1rem',
                                      borderRadius: '0.375rem',
                                      fontSize: '0.875rem',
                                    }}
                                  >
                                    {job.error}
                                  </div>
                                </div>
                              )}

                              {/* Events */}
                              {job.events && job.events.length > 0 && (
                                <div>
                                  <h4 style={{ marginBottom: '0.5rem', fontWeight: '600' }}>
                                    Events ({job.events.length})
                                  </h4>
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                    {job.events.map((event, idx) => (
                                      <div
                                        key={idx}
                                        style={{
                                          backgroundColor: 'var(--color-surface)',
                                          padding: '0.75rem',
                                          borderRadius: '0.375rem',
                                          borderLeft: `3px solid var(--color-primary)`,
                                          fontSize: '0.875rem',
                                          border: `1px solid var(--color-border)`,
                                          borderLeftWidth: '3px',
                                        }}
                                      >
                                        <div style={{ color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>
                                          {formatTime(event.timestamp)}
                                        </div>
                                        <div>{event.message}</div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
