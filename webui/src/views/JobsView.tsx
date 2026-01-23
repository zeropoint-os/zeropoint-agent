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

  const deleteAllJobs = async () => {
    try {
      // Use the generated API client to delete jobs based on current filter
      const statusParam = filter === 'all' ? 'all' : filter;
      await jobsApi.deleteJobs({ status: statusParam });
      await fetchJobs();
    } catch (err) {
      console.error('Error deleting jobs:', err);
      setError(err instanceof Error ? err.message : 'Failed to delete jobs');
    }
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
          <div style={{ marginBottom: '1.5rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
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
            {(filter === 'completed' || filter === 'failed') && sortedJobs.length > 0 && (
              <button
                className="button button-danger"
                onClick={() => {
                  if (window.confirm(`Delete all ${filter} jobs?`)) {
                    deleteAllJobs();
                  }
                }}
                style={{ marginLeft: 'auto' }}
              >
                Delete All
              </button>
            )}
          </div>

          {sortedJobs.length === 0 ? (
            <div className="empty-state">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect>
                <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path>
              </svg>
              <h2>No jobs found</h2>
              <p>Jobs will appear here as you perform actions.</p>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.5rem' }}>
              {sortedJobs.map((job) => (
                <div
                  key={job.id}
                  style={{
                    backgroundColor: 'var(--color-surface)',
                    border: `1px solid var(--color-border)`,
                    borderRadius: '0.5rem',
                    padding: '1.5rem',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    borderLeft: `4px solid ${getStatusColor(job.status || '')}`,
                  }}
                  onClick={() =>
                    setExpandedJobId(expandedJobId === job.id ? null : job.id ?? null)
                  }
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--color-surface-hover)';
                    (e.currentTarget as HTMLElement).style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--color-surface)';
                    (e.currentTarget as HTMLElement).style.boxShadow = 'none';
                  }}
                >
                  {/* Card Header */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: '1rem' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem', fontFamily: 'monospace' }}>
                        {job.id?.substring(0, 12)}...
                      </div>
                      <div style={{ fontSize: '1rem', fontWeight: '600' }}>
                        {job.command?.type || '-'}
                      </div>
                    </div>
                    <span style={{ fontSize: '1rem', color: 'var(--color-primary)' }}>
                      {expandedJobId === job.id ? '▼' : '▶'}
                    </span>
                  </div>

                  {/* Status Badge */}
                  <div style={{ marginBottom: '1rem' }}>
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
                  </div>

                  {/* Card Meta */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem', fontSize: '0.875rem' }}>
                    <div>
                      <div style={{ color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>Created</div>
                      <div>{formatDate(job.createdAt)}</div>
                      <div style={{ color: 'var(--color-text-secondary)', fontSize: '0.75rem' }}>
                        {formatTime(job.createdAt)}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>Duration</div>
                      <div>{getDuration(job.startedAt, job.completedAt)}</div>
                    </div>
                  </div>

                  {/* Expanded Details */}
                  {expandedJobId === job.id && (
                    <div style={{ borderTop: `1px solid var(--color-border)`, paddingTop: '1rem', marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {/* Command details */}
                      <div>
                        <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', fontSize: '0.875rem' }}>Command</h4>
                        <div
                          style={{
                            backgroundColor: 'var(--color-surface-alt)',
                            padding: '0.75rem',
                            borderRadius: '0.375rem',
                            fontFamily: 'monospace',
                            fontSize: '0.75rem',
                            overflowX: 'auto',
                            border: `1px solid var(--color-border)`,
                          }}
                        >
                          <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                            {JSON.stringify(job.command, null, 2)}
                          </pre>
                        </div>
                      </div>

                      {/* Dependencies */}
                      {job.dependsOn && job.dependsOn.length > 0 && (
                        <div>
                          <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', fontSize: '0.875rem' }}>
                            Depends On ({job.dependsOn.length})
                          </h4>
                          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                            {job.dependsOn.map((depId) => (
                              <span
                                key={depId}
                                style={{
                                  backgroundColor: 'var(--color-border)',
                                  padding: '0.25rem 0.5rem',
                                  borderRadius: '0.375rem',
                                  fontSize: '0.75rem',
                                  fontFamily: 'monospace',
                                }}
                              >
                                {depId.substring(0, 8)}...
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Tags */}
                      {job.tags && job.tags.length > 0 && (
                        <div>
                          <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', fontSize: '0.875rem' }}>
                            Tags
                          </h4>
                          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                            {job.tags.map((tag) => (
                              <span
                                key={tag}
                                style={{
                                  backgroundColor: 'var(--color-primary-light)',
                                  color: 'var(--color-primary)',
                                  padding: '0.25rem 0.5rem',
                                  borderRadius: '0.375rem',
                                  fontSize: '0.75rem',
                                  fontWeight: '500',
                                }}
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Error message */}
                      {job.error && (
                        <div>
                          <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', fontSize: '0.875rem', color: 'var(--color-danger)' }}>
                            Error
                          </h4>
                          <div
                            style={{
                              backgroundColor: 'var(--color-danger-light)',
                              color: 'var(--color-danger)',
                              padding: '0.75rem',
                              borderRadius: '0.375rem',
                              fontSize: '0.875rem',
                              border: `1px solid var(--color-danger)`,
                            }}
                          >
                            {job.error}
                          </div>
                        </div>
                      )}

                      {/* Events */}
                      {job.events && job.events.length > 0 && (
                        <div>
                          <h4 style={{ marginBottom: '0.5rem', fontWeight: '600', fontSize: '0.875rem' }}>
                            Events ({job.events.length})
                          </h4>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', maxHeight: '200px', overflowY: 'auto' }}>
                            {job.events.map((event, idx) => (
                              <div
                                key={idx}
                                style={{
                                  backgroundColor: 'var(--color-surface-alt)',
                                  padding: '0.5rem',
                                  borderRadius: '0.375rem',
                                  borderLeft: `3px solid var(--color-primary)`,
                                  fontSize: '0.75rem',
                                  border: `1px solid var(--color-border)`,
                                  borderLeftWidth: '3px',
                                }}
                              >
                                <div style={{ color: 'var(--color-text-secondary)', marginBottom: '0.25rem', fontSize: '0.7rem' }}>
                                  {formatTime(event.timestamp)}
                                </div>
                                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{event.message}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
