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

interface Exposure {
  id?: string;
  module_id?: string;
  protocol?: string;
  hostname?: string;
  container_port?: number;
  status?: string;
  [key: string]: any;
}

interface Link {
  id?: string;
  modules?: { [key: string]: any };
  [key: string]: any;
}

export default function ModulesView() {
  const [modules, setModules] = useState<Module[]>([]);
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uninstallingModule, setUninstallingModule] = useState<string | null>(null);
  const [installingModule, setInstallingModule] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);

  // Initialize API clients
  const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchModulesAndExposures();
  }, []);

  const fetchModulesAndExposures = async () => {
    try {
      setLoading(true);
      
      // Fetch modules
      const modulesResponse = await modulesApi.modulesGet();
      const modulesList = Array.isArray(modulesResponse.modules) ? modulesResponse.modules : [];
      setModules(modulesList);
      
      // Fetch exposures
      const exposuresResponse = await fetch('/api/exposures');
      if (exposuresResponse.ok) {
        const exposuresData = await exposuresResponse.json();
        const exposuresList = Array.isArray(exposuresData.exposures) ? exposuresData.exposures : [];
        setExposures(exposuresList);
      }

      // Fetch links
      const linksResponse = await fetch('/api/links');
      if (linksResponse.ok) {
        const linksData = await linksResponse.json();
        const linksList = Array.isArray(linksData.links) ? linksData.links : [];
        setLinks(linksList);
      }
      
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setModules([]);
      setExposures([]);
      setLinks([]);
    } finally {
      setLoading(false);
    }
  };

  const getModuleExposure = (moduleId: string | undefined): Exposure | undefined => {
    if (!moduleId) return undefined;
    return exposures.find(exp => exp.module_id === moduleId);
  };

  const getLinkedModules = (moduleId: string | undefined): string[] => {
    if (!moduleId) return [];
    const linkedModules: string[] = [];
    
    links.forEach(link => {
      if (link.modules && typeof link.modules === 'object') {
        if (moduleId in link.modules) {
          // This module is part of this link, find other modules in it
          Object.keys(link.modules).forEach(modId => {
            if (modId !== moduleId && !linkedModules.includes(modId)) {
              linkedModules.push(modId);
            }
          });
        }
      }
    });
    
    return linkedModules; // Already deduplicated
  };

  const hasModuleDependencies = (moduleId: string | undefined): boolean => {
    if (!moduleId) return false;
    const hasExposures = exposures.some(e => e.module_id === moduleId);
    const hasLinks = links.some(link => link.modules && moduleId in link.modules);
    return hasExposures || hasLinks;
  };

  const getExposureUrl = (exposure: Exposure): string => {
    if (!exposure.protocol || !exposure.hostname || !exposure.container_port) {
      return 'N/A';
    }
    return `${exposure.protocol}://${exposure.hostname}:${exposure.container_port}`;
  };

  const getModulePorts = (module: Module): Array<{name: string; port: number; protocol: string}> => {
    const ports: Array<{name: string; port: number; protocol: string}> = [];
    if (module.containers && typeof module.containers === 'object') {
      Object.values(module.containers).forEach((container: any) => {
        if (container.ports && typeof container.ports === 'object') {
          Object.entries(container.ports).forEach(([portName, portInfo]: [string, any]) => {
            if (portInfo.port && portInfo.protocol) {
              ports.push({
                name: portName,
                port: portInfo.port,
                protocol: portInfo.protocol
              });
            }
          });
        }
      });
    }
    return ports;
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
      setProgressMessage(null);

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
                setProgressMessage(message);
              } catch {
                if (line.trim()) {
                  setProgressMessage(line);
                }
              }
            }
          }
        }
      }

      // Refresh modules and exposures list
      await fetchModulesAndExposures();
      
      setSuccessMessage(`${moduleName} installed successfully`);
      setTimeout(() => setSuccessMessage(null), 4000);
      
    } catch (err) {
      console.error('Install error:', err);
      setError(err instanceof Error ? err.message : 'Failed to install module');
      await fetchModulesAndExposures();
    } finally {
      setInstallingModule(null);
      setTimeout(() => setProgressMessage(null), 2000);
    }
  };

  const handleUninstall = async (moduleName: string) => {
    if (!moduleName) {
      setError('Cannot uninstall: module name is missing');
      return;
    }

    // Check for exposures and links
    const moduleExposures = exposures.filter(e => e.module_id === moduleName);
    const moduleLinks = links.filter(link => link.modules && moduleName in link.modules);

    if (moduleExposures.length > 0 || moduleLinks.length > 0) {
      const reasons: string[] = [];
      if (moduleExposures.length > 0) {
        reasons.push(`${moduleExposures.length} exposure${moduleExposures.length > 1 ? 's' : ''}`);
      }
      if (moduleLinks.length > 0) {
        reasons.push(`${moduleLinks.length} link${moduleLinks.length > 1 ? 's' : ''}`);
      }
      setError(`Cannot uninstall ${moduleName}: it has ${reasons.join(' and ')}. Please remove them first.`);
      return;
    }

    if (!window.confirm(`Are you sure you want to uninstall ${moduleName}?`)) {
      return;
    }

    try {
      setUninstallingModule(moduleName);
      setError(null);
      setProgressMessage(null);

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
                setProgressMessage(message);
              } catch {
                if (line.trim()) {
                  setProgressMessage(line);
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
      await fetchModulesAndExposures();
    } finally {
      setUninstallingModule(null);
      setTimeout(() => setProgressMessage(null), 2000);
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
          <button className="button button-secondary" onClick={fetchModulesAndExposures}>
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
            {progressMessage && (
              <div style={{ fontSize: '0.85rem', opacity: 0.9, marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.2)' }}>
                {progressMessage}
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
            const ports = getModulePorts(module);
            const exposure = getModuleExposure(module.id);
            const isExposed = exposure ? 'Yes' : 'No';
            const linkedModules = getLinkedModules(module.id);
            return (
              <div key={key} className="card">
                <div className="module-header">
                  <div>
                    <h3 className="module-name">{module.id || 'Unnamed Module'}</h3>
                    <p className="module-version">Exposed: {isExposed}</p>
                    {linkedModules.length > 0 && (
                      <p className="module-links">Linked to: {linkedModules.join(', ')}</p>
                    )}
                  </div>
                  <div className={`status-badge status-${state.toLowerCase()}`}>
                    {state}
                  </div>
                </div>

              {module.container_name && (
                <div className="module-detail">
                  <span className="detail-label">Container:</span>
                  <span className="detail-value">{module.container_name}</span>
                </div>
              )}

              {module.ip_address && (
                <div className="module-detail">
                  <span className="detail-label">IP Address:</span>
                  <span className="detail-value">{module.ip_address}</span>
                </div>
              )}

              {ports.length > 0 && (
                <div className="module-ports">
                  <span className="detail-label">Ports:</span>
                  <div className="ports-list">
                    {ports.map((p, idx) => (
                      <div key={idx} className="port-item">
                        <span className="port-name">{p.name}</span>
                        <span className="port-value">{p.port}/{p.protocol}</span>
                      </div>
                    ))}
                  </div>
                </div>
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
                  <button
                    className="button button-danger"
                    onClick={() => handleUninstall(module.id || '')}
                    disabled={uninstallingModule === module.id || hasModuleDependencies(module.id)}
                    title={hasModuleDependencies(module.id) ? 'Module has exposures or links - remove them first' : ''}
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
