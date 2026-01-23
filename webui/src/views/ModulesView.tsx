import React, { useState, useEffect, useRef } from 'react';
import { ModulesApi, ExposuresApi, LinksApi, CatalogApi, JobsApi, Configuration, ApiModule, ApiExposureResponse, ApiLink } from 'artifacts/clients/typescript';
import type { CatalogModuleResponse } from 'artifacts/clients/typescript';
import CatalogBrowser from '../components/CatalogBrowser';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

type Module = ApiModule;
type Exposure = ApiExposureResponse;
type Link = ApiLink;

export default function ModulesView() {
  const [modules, setModules] = useState<Module[]>([]);
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const loadingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
  const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
  const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
  const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchModulesAndExposures();
    // Refresh every 5 seconds
    const interval = setInterval(fetchModulesAndExposures, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchModulesAndExposures = async () => {
    loadingTimeoutRef.current = setTimeout(() => {
      setLoading(true);
    }, LOADING_INDICATOR_DELAY);

    try {
      const [modulesRes, exposuresRes, linksRes] = await Promise.all([
        modulesApi.listModules(),
        exposuresApi.listExposures(),
        linksApi.listLinks(),
      ]);

      setModules(Array.isArray(modulesRes.modules) ? modulesRes.modules : []);
      setExposures(exposuresRes.exposures ?? []);
      setLinks(linksRes.links ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
      }
      setLoading(false);
    }
  };

  const getModuleExposure = (moduleId: string | undefined): Exposure | undefined => {
    if (!moduleId) return undefined;
    return exposures.find(exp => exp.moduleId === moduleId);
  };

  const getLinkedModules = (moduleId: string | undefined): string[] => {
    if (!moduleId) return [];
    const linkedModules: string[] = [];
    links.forEach(link => {
      if (link.modules && typeof link.modules === 'object') {
        if (moduleId in link.modules) {
          Object.keys(link.modules).forEach(modId => {
            if (modId !== moduleId && !linkedModules.includes(modId)) {
              linkedModules.push(modId);
            }
          });
        }
      }
    });
    return linkedModules;
  };

  const hasModuleDependencies = (moduleId: string | undefined): boolean => {
    if (!moduleId) return false;
    const hasExposures = exposures.some(e => e.moduleId === moduleId);
    const hasLinks = links.some(link => link.modules && moduleId in link.modules);
    return hasExposures || hasLinks;
  };

  const getExposureUrl = (exposure: Exposure): string => {
    if (!exposure.protocol || !exposure.id) {
      return 'N/A';
    }
    // All exposures go through Envoy on port 80, mDNS is configured for the exposure ID
    return `${exposure.protocol}://${exposure.id}.local/`;
  };

  const handleInstallModule = async (moduleName: string) => {
    if (!window.confirm(`Install module "${moduleName}"?`)) {
      return;
    }

    try {
      const moduleInfo = await catalogApi.getCatalogModule({ moduleName });
      await jobsApi.enqueueInstall({
        queueEnqueueInstallRequest: {
          moduleId: moduleName,
          source: moduleInfo.source || '',
        }
      });
      setError(null);
      setShowInstallDialog(false);
      // Refresh after a short delay
      setTimeout(() => fetchModulesAndExposures(), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install module');
    }
  };

  const handleUninstallModule = async (moduleId: string) => {
    if (hasModuleDependencies(moduleId)) {
      setError(`Cannot uninstall "${moduleId}" - it has active exposures or links. Remove those first.`);
      return;
    }

    if (!window.confirm(`Uninstall module "${moduleId}"?`)) {
      return;
    }

    try {
      await jobsApi.enqueueUninstall({
        queueEnqueueUninstallRequest: {
          moduleId: moduleId,
        }
      });
      setError(null);
      setTimeout(() => fetchModulesAndExposures(), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to uninstall module');
    }
  };

  return (
    <div className="view-container">
      {showInstallDialog && (
        <CatalogBrowser
          filterType="modules"
          onClose={() => setShowInstallDialog(false)}
          onSelect={(module: CatalogModuleResponse) => {
            if (module.name) handleInstallModule(module.name);
          }}
        />
      )}

      <div className="view-header">
        <h1 className="section-title">Modules</h1>
        {modules.length > 0 && (
          <button
            className="button button-primary"
            onClick={() => setShowInstallDialog(true)}
          >
            <span>+</span> Install Module
          </button>
        )}
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
          <p>Loading modules...</p>
        </div>
      ) : modules.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7"></rect>
            <rect x="14" y="3" width="7" height="7"></rect>
            <rect x="14" y="14" width="7" height="7"></rect>
            <rect x="3" y="14" width="7" height="7"></rect>
          </svg>
          <h2>No modules installed</h2>
          <p>Install modules to add functionality to your system.</p>
          <button
            className="button button-primary"
            onClick={() => setShowInstallDialog(true)}
          >
            Browse Modules
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {modules.map((module, idx) => {
            const exposure = getModuleExposure(module.id);
            const linkedModules = getLinkedModules(module.id);
            const key = module.id || `module-${idx}`;

            return (
              <div key={key} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
                <div style={{ flex: 1 }}>
                  <div className="module-header">
                    <h3 className="module-name">{module.id || 'Unnamed'}</h3>
                    <span style={{ fontSize: '0.75rem', fontWeight: '600', padding: '0.25rem 0.75rem', borderRadius: '0.375rem', backgroundColor: module.state === 'running' ? 'var(--color-success-light)' : 'var(--color-border)', color: module.state === 'running' ? 'var(--color-success-dark)' : 'var(--color-text-secondary)' }}>
                      {module.state || 'unknown'}
                    </span>
                    {exposure && (
                      <a
                        href={getExposureUrl(exposure)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="link"
                        style={{ fontSize: '0.875rem', marginLeft: '0.5rem' }}
                      >
                        ↗ Open
                      </a>
                    )}
                  </div>

                  {/* Container and Network Info */}
                  {(module.containerName || module.ipAddress) && (
                    <div style={{ marginBottom: '0.75rem', fontSize: '0.875rem' }}>
                      {module.containerName && (
                        <div style={{ marginBottom: '0.25rem' }}>
                          <span style={{ color: 'var(--color-text-secondary)' }}>Container:</span> <span style={{ fontFamily: 'monospace' }}>{module.containerName}</span>
                        </div>
                      )}
                      {module.ipAddress && (
                        <div>
                          <span style={{ color: 'var(--color-text-secondary)' }}>IP Address:</span> <span style={{ fontFamily: 'monospace' }}>{module.ipAddress}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* GPU Info */}
                  {module.gpuVendor && (
                    <div style={{ marginBottom: '0.75rem', fontSize: '0.875rem' }}>
                      <div style={{ marginBottom: '0.25rem' }}>
                        <span style={{ color: 'var(--color-text-secondary)' }}>GPU Vendor:</span> <span>{module.gpuVendor}</span>
                      </div>
                      {module.usingGpu !== undefined && (
                        <div>
                          <span style={{ color: 'var(--color-text-secondary)' }}>GPU Usage:</span> <span>{module.usingGpu ? 'Enabled' : 'Disabled'}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Ports */}
                  {module.containers && Object.keys(module.containers).length > 0 && (
                    <div style={{ marginBottom: '0.75rem', fontSize: '0.875rem' }}>
                      <p style={{ margin: '0 0 0.5rem 0', fontWeight: '500', color: 'var(--color-text-secondary)' }}>Ports:</p>
                      {Object.entries(module.containers).map(([containerName, container]) =>
                        container.ports && Object.entries(container.ports).length > 0 ? (
                          Object.entries(container.ports).map(([portName, portInfo]) => (
                            <div key={`${containerName}-${portName}`} style={{ marginLeft: '0.5rem', marginBottom: '0.25rem' }}>
                              <span>{portName}</span> <span style={{ fontFamily: 'monospace', color: 'var(--color-text-secondary)' }}>{portInfo.port}/{portInfo.protocol || 'tcp'}</span>
                            </div>
                          ))
                        ) : null
                      )}
                    </div>
                  )}

                  {linkedModules.length > 0 && (
                    <div style={{ marginBottom: '0.75rem' }}>
                      <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.5rem' }}>
                        Linked to:
                      </p>
                      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                        {linkedModules.map(mod => (
                          <span
                            key={mod}
                            style={{
                              fontSize: '0.75rem',
                              backgroundColor: 'var(--color-info-light)',
                              color: 'var(--color-info)',
                              padding: '0.25rem 0.5rem',
                              borderRadius: '0.25rem',
                            }}
                          >
                            {mod}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {exposure && (
                    <div style={{ marginBottom: '0.75rem', padding: '0.75rem', backgroundColor: 'var(--color-success-light)', borderRadius: '0.375rem' }}>
                      <p style={{ fontSize: '0.875rem', fontWeight: '500', color: 'var(--color-success-dark)', marginBottom: '0.25rem' }}>
                        Exposed: {exposure.id}
                      </p>
                      <p style={{ fontSize: '0.75rem', color: 'var(--color-success-dark)', margin: 0 }}>
                        {getExposureUrl(exposure)}
                      </p>
                    </div>
                  )}
                </div>

                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    className="button button-danger"
                    onClick={() => handleUninstallModule(module.id || '')}
                    disabled={hasModuleDependencies(module.id)}
                    style={{ flex: 1 }}
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
