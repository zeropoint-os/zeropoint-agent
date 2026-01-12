import React, { useState, useEffect } from 'react';
import './Views.css';

interface Module {
  id?: string;
  module_path?: string;
  state?: string;
  container_id?: string;
  container_name?: string;
  ip_address?: string;
  containers?: any;
  tags?: string[];
  [key: string]: any;
}

export default function ModulesView() {
  const [modules, setModules] = useState<Module[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchModules();
  }, []);

  const fetchModules = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/modules');
      if (!response.ok) {
        throw new Error(`Failed to fetch modules: ${response.statusText}`);
      }
      const data = await response.json();
      // Handle the response structure - could be array or object with modules property
      const modulesList = Array.isArray(data) ? data : (data.modules || data.data || []);
      setModules(modulesList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setModules([]);
    } finally {
      setLoading(false);
    }
  };

  const handleInstall = () => {
    // TODO: Show install modal
    console.log('Install module');
  };

  const handleUninstall = (moduleName: string) => {
    // TODO: Show uninstall confirmation
    console.log('Uninstall module:', moduleName);
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Modules</h1>
        <button className="button button-primary" onClick={handleInstall}>
          <span>+</span> Install Module
        </button>
      </div>

      {loading && (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading modules...</p>
        </div>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={fetchModules}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && modules.length === 0 && (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7"></rect>
            <rect x="14" y="3" width="7" height="7"></rect>
            <rect x="14" y="14" width="7" height="7"></rect>
            <rect x="3" y="14" width="7" height="7"></rect>
          </svg>
          <h2>No modules installed</h2>
          <p>Get started by installing your first module.</p>
          <button className="button button-primary" onClick={handleInstall}>
            Install Module
          </button>
        </div>
      )}

      {!loading && !error && modules.length > 0 && (
        <div className="grid grid-2">
          {modules.map((module, idx) => {
            const key = module.id || `module-${idx}`;
            const state = module.state || 'unknown';
            return (
              <div key={key} className="card">
                <div className="module-header">
                  <div>
                    <h3 className="module-name">{module.id || 'Unnamed Module'}</h3>
                    <p className="module-version">{module.module_path || 'N/A'}</p>
                  </div>
                  <div className={`status-badge status-${state.toLowerCase()}`}>
                    {state}
                  </div>
                </div>

              {module.description && (
                <p className="module-description">{module.description}</p>
              )}

              {module.tags && module.tags.length > 0 && (
                <div className="module-tags">
                  {module.tags.map((tag) => (
                    <span key={tag} className="tag">
                      {tag}
                    </span>
                  ))}
                </div>
              )}

                <div className="module-actions">
                  <button className="button button-secondary">View Details</button>
                  <button
                    className="button button-danger"
                    onClick={() => handleUninstall(module.id || '')}
                  >
                    Uninstall
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
