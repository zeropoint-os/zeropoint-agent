import React, { useState, useEffect } from 'react';
import { LinksApi, Configuration, ApiLink } from 'artifacts/clients/typescript';
import CreateLinkDialog from '../components/CreateLinkDialog';
import './Views.css';

type Link = ApiLink;

export default function LinksView() {
  const [links, setLinks] = useState<ApiLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingLink, setDeletingLink] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  useEffect(() => {
    fetchLinks();
  }, []);

  const fetchLinks = async () => {
    try {
      setLoading(true);
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const response = await linksApi.listLinks();
      const linkList = response.links ?? [];
      setLinks(linkList);
      setError(null);
    } catch (err) {
      console.error('Error loading links:', err);
      setError(err instanceof Error ? err.message : 'Unknown error');
      setLinks([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateLink = () => {
    setShowCreateDialog(true);
  };

  const handleCreateLinkSubmit = async (data: {
    id: string;
    modules: { [moduleId: string]: { [key: string]: string } };
  }) => {
    try {
      // Refresh links list after successful creation
      await fetchLinks();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh links');
      throw err;
    }
  };

  const handleDeleteLink = async (linkId: string) => {
    if (!window.confirm(`Are you sure you want to delete link "${linkId}"?`)) {
      return;
    }

    try {
      setDeletingLink(linkId);
      setError(null);

      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      await linksApi.deleteLink({ id: linkId });

      // Refresh links list
      await fetchLinks();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete link');
    } finally {
      setDeletingLink(null);
    }
  };

  return (
    <div className="view-container">
      <CreateLinkDialog
        isOpen={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreate={handleCreateLinkSubmit}
      />

      <div className="view-header">
        <h1 className="section-title">Links</h1>
        <button className="button button-primary" onClick={handleCreateLink}>
          <span>+</span> Create Link
        </button>
      </div>

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
          <p>Create links between modules to establish connections.</p>
          <button className="button button-primary" onClick={handleCreateLink}>
            Create Link
          </button>
        </div>
      ) : (
        <div className="grid grid-1">
          {links.map((link) => {
            const linkId = link.id || 'unknown';
            const moduleIds = Object.keys(link.modules || {});
            const createdDate = link.createdAt 
              ? new Date(link.createdAt).toLocaleDateString()
              : 'N/A';

            return (
              <div key={linkId} className="card">
                <div className="link-header">
                  <div>
                    <h3 className="link-title">{linkId}</h3>
                    <p className="link-created">Created: {createdDate}</p>
                  </div>
                </div>

                <div className="link-modules-section">
                  <h4 className="section-label">Modules</h4>
                  <div className="modules-list">
                    {moduleIds.map((moduleId, idx) => (
                      <div key={moduleId} className="module-item">
                        <span className="module-name">{moduleId}</span>
                        {link.references?.[moduleId] && (
                          <div className="module-references">
                            {Object.entries(link.references[moduleId]).map(([refKey, refValue]) => (
                              <div key={refKey} className="reference-item">
                                <span className="ref-label">{refKey}:</span>
                                <code className="ref-value">{refValue as string}</code>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {link.dependencyOrder && link.dependencyOrder.length > 0 && (
                  <div className="link-dependencies-section">
                    <h4 className="section-label">Dependency Order</h4>
                    <div className="dependency-flow">
                      {link.dependencyOrder.map((dep, idx) => (
                        <React.Fragment key={dep}>
                          <span className="dependency-item">{dep}</span>
                          {idx < link.dependencyOrder!.length - 1 && (
                            <span className="dependency-arrow">→</span>
                          )}
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                )}

                {link.sharedNetworks && link.sharedNetworks.length > 0 && (
                  <div className="link-networks-section">
                    <h4 className="section-label">Networks</h4>
                    <div className="networks-list">
                      {link.sharedNetworks.map((network) => (
                        <span key={network} className="tag">{network}</span>
                      ))}
                    </div>
                  </div>
                )}

                {link.tags && link.tags.length > 0 && (
                  <div className="link-tags">
                    {link.tags.map((tag) => (
                      <span key={tag} className="tag">{tag}</span>
                    ))}
                  </div>
                )}

                <div className="link-actions">
                  <button
                    className="button button-danger"
                    onClick={() => handleDeleteLink(linkId)}
                    disabled={deletingLink === linkId}
                  >
                    {deletingLink === linkId ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
