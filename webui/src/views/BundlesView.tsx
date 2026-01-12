import React, { useState, useEffect } from 'react';
import './Views.css';

interface Bundle {
  name?: string;
  version?: string;
  status?: string;
  modules?: string[];
  tags?: string[];
  [key: string]: any;
}

export default function BundlesView() {
  const [bundles, setBundles] = useState<Bundle[]>([]);

  useEffect(() => {
    // Bundles endpoint may not be implemented yet
  }, []);

  const handleCreateBundle = () => {
    // TODO: Show create bundle modal
    console.log('Create bundle');
  };

  const handleDeleteBundle = (bundleName: string) => {
    // TODO: Show delete confirmation
    console.log('Delete bundle:', bundleName);
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Bundles</h1>
        <button className="button button-primary" onClick={handleCreateBundle}>
          <span>+</span> Create Bundle
        </button>
      </div>

      {bundles.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6.2 2h11.6c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H6.2c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2z"></path>
            <path d="M12 10v6M9 13h6"></path>
          </svg>
          <h2>No bundles</h2>
          <p>Group modules together into bundles for easier management.</p>
          <button className="button button-primary" onClick={handleCreateBundle}>
            Create Bundle
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {bundles.map((bundle, idx) => {
            const key = bundle.name || `bundle-${idx}`;
            return (
              <div key={key} className="card">
                <div className="bundle-header">
                  <h3 className="bundle-name">{bundle.name || 'Unnamed'}</h3>
                  <div className={`status-badge status-${(bundle.status || 'unknown').toLowerCase()}`}>
                    {bundle.status || 'unknown'}
                  </div>
                </div>
                <p className="bundle-version">v{bundle.version || 'unknown'}</p>
                {bundle.modules && bundle.modules.length > 0 && (
                  <div className="bundle-modules">
                    <h4>Modules:</h4>
                    <ul>
                      {bundle.modules.map((module) => (
                        <li key={module}>{module}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {bundle.tags && bundle.tags.length > 0 && (
                  <div className="bundle-tags">
                    {bundle.tags.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="bundle-actions">
                  <button className="button button-secondary">Edit</button>
                  <button
                    className="button button-danger"
                    onClick={() => handleDeleteBundle(bundle.name || '')}
                  >
                    Delete
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
