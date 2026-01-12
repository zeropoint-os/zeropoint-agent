import React, { useState, useEffect } from 'react';
import { ModulesApi, CatalogApi, Configuration } from 'artifacts/clients/typescript';
import type { CatalogModuleResponse } from 'artifacts/clients/typescript';
import CatalogBrowser from '../components/CatalogBrowser';
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
  const [uninstallingModule, setUninstallingModule] = useState<string | null>(null);
  const [installingModule, setInstallingModule] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const [progressMessages, setProgressMessages] = useState<string[]>([]);

  // Initialize API clients
  const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchModules();
  }, []);

  const fetchModules = async () => {
    try {
      setLoading(true);
      const response = await modulesApi.modulesGet();
      const modulesList = Array.isArray(response.modules) ? response.modules : [];
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
    setShowInstallDialog(true);
  };

  const handleSelectCatalogItem = async (item: CatalogModuleResponse) => {
    const moduleName = item.name || '';
    
    if (!moduleName) {
      setError('Invalid module: name is missing');
      setShowInstallDialog(true);
      return;
    }

    try {
      setInstallingModule(moduleName);
      setShowInstallDialog(false);
      setError(null);
      setProgressMessages([]);

      // Make raw fetch call to handle streaming response
      const response = await fetch(`/api/modules/${moduleName}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          source: item.source,
          module_id: moduleName,
          tags: undefined
        })
      });

      if (!response.ok) {
        throw new Error(`Failed to install module: ${response.statusText}`);
      }

      // Process the streaming response
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      
      if (reader) {
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          
          for (const line of lines) {
            if (line.trim()) {
              try {
                const data = JSON.parse(line);
                const message = data.message || data.status || line;
                setProgressMessages(prev => [...prev, message]);
              } catch {
                if (line.trim()) {
                  setProgressMessages(prev => [...prev, line]);
                }
              }
            }
          }
        }
      }

      // Refresh modules list
      await fetchModules();
      
      setSuccessMessage(`${moduleName} installed successfully`);
      setTimeout(() => setSuccessMessage(null), 4000);
      
    } catch (err) {
      console.error('Install error:', err);
      setError(err instanceof Error ? err.message : 'Failed to install module');
      await fetchModules();
    } finally {
      setInstallingModule(null);
      setTimeout(() => setProgressMessages([]), 2000);
    }
  };

  const handleUninstall = async (moduleName: string) => {
    if (!moduleName) {
      setError('Cannot uninstall: module name is missing');
      return;
    }

    if (!window.confirm(`Are you sure you want to uninstall ${moduleName}?`)) {
      return;
    }

    try {
      setUninstallingModule(moduleName);
      setError(null);
      setProgressMessages([]);

      // Make raw fetch call to handle streaming response
      const response = await fetch(`/api/modules/${moduleName}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error(`Failed to uninstall module: ${response.statusText}`);
      }

      // Process the streaming response
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      
      if (reader) {
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          
          for (const line of lines) {
            if (line.trim()) {
              try {
                const data = JSON.parse(line);
                const message = data.message || data.status || line;
                setProgressMessages(prev => [...prev, message]);
              } catch {
                if (line.trim()) {
                  setProgressMessages(prev => [...prev, line]);
                }
              }
            }
          }
        }
      }

      // Remove from state after stream completes
      setModules(modules.filter(m => m.id !== moduleName));
      
      setSuccessMessage(`${moduleName} uninstalled successfully`);
      setTimeout(() => setSuccessMessage(null), 4000);
      
    } catch (err) {
      console.error('Uninstall error:', err);
      setError(err instanceof Error ? err.message : 'Failed to uninstall module');
      await fetchModules();
    } finally {
      setUninstallingModule(null);
      setTimeout(() => setProgressMessages([]), 2000);
    }
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Modules</h1>
        {modules.length > 0 && (
          <button className="button button-primary" onClick={handleInstall}>
            <span>+</span> Install Module
          </button>
        )}
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

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {successMessage && (
        <div className="success-state">
          <p className="success-message">✓ {successMessage}</p>
        </div>
      )}

      {installingModule && (
        <div className="progress-state" style={{ position: 'fixed', top: '80px', left: '50%', transform: 'translateX(-50%)', zIndex: 100, minWidth: '300px', maxWidth: '600px' }}>
          <div style={{ padding: '1rem', backgroundColor: 'var(--color-primary)', color: 'white', borderRadius: 'var(--border-radius-md)', boxShadow: 'var(--shadow-lg)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <div className="spinner" style={{ width: '20px', height: '20px', borderWidth: '2px' }}></div>
              <span style={{ fontSize: '0.95rem', fontWeight: '500' }}>Installing {installingModule}...</span>
            </div>
            {progressMessages.length > 0 && (
              <div style={{ fontSize: '0.85rem', opacity: 0.9, maxHeight: '200px', overflowY: 'auto', borderTop: '1px solid rgba(255,255,255,0.2)', paddingTop: '0.5rem', marginTop: '0.5rem' }}>
                {progressMessages.map((msg, idx) => (
                  <div key={idx} style={{ margin: '0.25rem 0' }}>{msg}</div>
                ))}
              </div>
            )}
          </div>
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
                    disabled={uninstallingModule === module.id}
                  >
                    {uninstallingModule === module.id ? 'Uninstalling...' : 'Uninstall'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showInstallDialog && (
        <CatalogBrowser 
          filterType="modules"
          onSelect={handleSelectCatalogItem}
          onClose={() => setShowInstallDialog(false)}
        />
      )}
    </div>
  );
}
