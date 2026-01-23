import React, { useState, useEffect, useRef } from 'react';
import { LinksApi, JobsApi, Configuration, ApiLink } from 'artifacts/clients/typescript';
import CreateLinkDialog from '../components/CreateLinkDialog';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

type Link = ApiLink;

export default function LinksView() {
  const [links, setLinks] = useState<ApiLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const loadingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchLinks();
    // Refresh every 5 seconds
    const interval = setInterval(fetchLinks, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchLinks = async () => {
    loadingTimeoutRef.current = setTimeout(() => {
      setLoading(true);
    }, LOADING_INDICATOR_DELAY);

    try {
      const response = await linksApi.listLinks();
      setLinks(response.links ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setLinks([]);
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
      }
      setLoading(false);
    }
  };

  const handleDeleteLink = async (linkId: string) => {
    if (!window.confirm(`Delete link "${linkId}"?`)) {
      return;
    }

    try {
      await jobsApi.enqueueDeleteLink({
        queueEnqueueDeleteLinkRequest: {
          linkId: linkId,
        }
      });
      setError(null);
      setTimeout(() => fetchLinks(), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete link');
    }
  };

  return (
    <div className="view-container">
      {showCreateDialog && (
        <CreateLinkDialog
          isOpen={showCreateDialog}
          onClose={() => setShowCreateDialog(false)}
          onCreate={async () => {
            setShowCreateDialog(false);
            setTimeout(() => fetchLinks(), 1000);
          }}
        />
      )}

      {links.length > 0 && (
        <div className="view-header">
          <h1 className="section-title">Links</h1>
          <button
            className="button button-primary"
            onClick={() => setShowCreateDialog(true)}
          >
            <span>+</span> Create Link
          </button>
        </div>
      )}

      {links.length === 0 && (
        <h1 className="section-title">Links</h1>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading links...</p>
        </div>
      ) : links.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
          </svg>
          <h2>No links created</h2>
          <p>Create links to connect modules together.</p>
          <button
            className="button button-primary"
            onClick={() => setShowCreateDialog(true)}
          >
            Create Link
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {links.map((link, idx) => {
            const key = link.id || `link-${idx}`;
            const moduleCount = link.modules ? Object.keys(link.modules).length : 0;

            return (
              <div key={key} className="card">
                <div style={{ marginBottom: '1rem' }}>
                  <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.125rem', fontWeight: '600' }}>
                    {link.id || 'Unnamed'}
                  </h3>
                  {link.createdAt && (
                    <p style={{ margin: '0', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                      Created: {new Date(link.createdAt).toLocaleDateString()}
                    </p>
                  )}
                </div>

                {link.modules && Object.keys(link.modules).length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.5rem' }}>
                      Connected Modules ({moduleCount}):
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      {Object.keys(link.modules).map((moduleId) => (
                        <span
                          key={moduleId}
                          style={{
                            fontSize: '0.875rem',
                            backgroundColor: 'var(--color-info-light)',
                            color: 'var(--color-info)',
                            padding: '0.5rem',
                            borderRadius: '0.375rem',
                          }}
                        >
                          {moduleId}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {link.dependencyOrder && link.dependencyOrder.length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.5rem' }}>
                      Dependency Order:
                    </p>
                    <div style={{ fontSize: '0.875rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                      {link.dependencyOrder.map((moduleName, idx) => (
                        <React.Fragment key={moduleName}>
                          <span style={{ backgroundColor: 'var(--color-surface-alt)', padding: '0.25rem 0.5rem', borderRadius: '0.25rem' }}>
                            {moduleName}
                          </span>
                          {idx < link.dependencyOrder!.length - 1 && (
                            <span style={{ color: 'var(--color-text-secondary)' }}>→</span>
                          )}
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                )}

                {link.references && Object.keys(link.references).length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.5rem' }}>
                      References:
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      {Object.entries(link.references).map(([moduleName, refs]) => (
                        <div key={moduleName} style={{ fontSize: '0.75rem' }}>
                          <div style={{ fontWeight: '500', marginBottom: '0.25rem' }}>{moduleName}:</div>
                          <div style={{ marginLeft: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                            {Object.entries(refs).map(([key, value]) => (
                              <div key={key} style={{ color: 'var(--color-text-secondary)' }}>
                                <span style={{ fontFamily: 'monospace' }}>{key}</span>: <span style={{ color: 'var(--color-info)' }}>{value as string}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {link.sharedNetworks && link.sharedNetworks.length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.5rem' }}>
                      Shared Networks:
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      {link.sharedNetworks.map((network) => (
                        <span
                          key={network}
                          style={{
                            fontSize: '0.875rem',
                            backgroundColor: 'var(--color-surface-alt)',
                            color: 'var(--color-text)',
                            padding: '0.5rem',
                            borderRadius: '0.375rem',
                            fontFamily: 'monospace',
                          }}
                        >
                          {network}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {link.tags && link.tags.length > 0 && (
                  <div style={{ marginBottom: '1rem' }}>
                    <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>
                      Tags:
                    </p>
                    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                      {link.tags.map((tag) => (
                        <span
                          key={tag}
                          style={{
                            fontSize: '0.75rem',
                            backgroundColor: 'var(--color-border)',
                            color: 'var(--color-text)',
                            padding: '0.25rem 0.5rem',
                            borderRadius: '0.25rem',
                          }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <button
                  className="button button-danger"
                  onClick={() => handleDeleteLink(link.id || '')}
                  style={{ width: '100%' }}
                >
                  Delete
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
