import React, { useState, useEffect } from 'react';
import { ModulesApi, ExposuresApi, LinksApi, CatalogApi, JobsApi, Configuration, ApiModule, ApiExposureResponse, ApiLink } from 'artifacts/clients/typescript';
import type { CatalogModuleResponse, QueueJobResponse } from 'artifacts/clients/typescript';
import CatalogBrowser from '../components/CatalogBrowser';
import InstallationProgress from '../components/InstallationProgress';
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
  const [uninstallingModule, setUninstallingModule] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  
  // Job queue state
  const [installJobs, setInstallJobs] = useState<Map<string, QueueJobResponse>>(new Map());
  const [uninstallJobs, setUninstallJobs] = useState<Map<string, QueueJobResponse>>(new Map());

  // Initialize API clients
  const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
  const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
  const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchModulesAndExposures();
  }, []);

  // Poll for job status updates
  useEffect(() => {
    const pollInstallJobs = async () => {
      const updatedJobs = new Map(installJobs);
      let jobsChanged = false;

      for (const [moduleName, job] of installJobs.entries()) {
        if (job.id && (job.status === 'queued' || job.status === 'running')) {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            if (updatedJob.id) {
              updatedJobs.set(moduleName, updatedJob);
              jobsChanged = true;

              // If job completed, refresh modules and remove from polling
              if (updatedJob.status === 'completed') {
                await fetchModulesAndExposures();
                setSuccessMessage(`${moduleName} installed successfully`);
                setTimeout(() => setSuccessMessage(null), 4000);
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
              // If job failed or cancelled, remove from polling
              else if (updatedJob.status === 'failed') {
                setError(`Failed to install ${moduleName}: ${updatedJob.error || 'Unknown error'}`);
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
              else if (updatedJob.status === 'cancelled') {
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
            }
          } catch (err) {
            console.error(`Error polling job for ${moduleName}:`, err);
          }
        }
      }

      if (jobsChanged) {
        setInstallJobs(updatedJobs);
      }
    };

    const pollUninstallJobs = async () => {
      const updatedJobs = new Map(uninstallJobs);
      let jobsChanged = false;

      for (const [moduleName, job] of uninstallJobs.entries()) {
        if (job.id && (job.status === 'queued' || job.status === 'running')) {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            if (updatedJob.id) {
              updatedJobs.set(moduleName, updatedJob);
              jobsChanged = true;

              // If job completed, refresh modules and remove from polling
              if (updatedJob.status === 'completed') {
                await fetchModulesAndExposures();
                setSuccessMessage(`${moduleName} uninstalled successfully`);
                setTimeout(() => setSuccessMessage(null), 4000);
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
              // If job failed or cancelled, remove from polling
              else if (updatedJob.status === 'failed') {
                setError(`Failed to uninstall ${moduleName}: ${updatedJob.error || 'Unknown error'}`);
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
              else if (updatedJob.status === 'cancelled') {
                updatedJobs.delete(moduleName);
                jobsChanged = true;
              }
            }
          } catch (err) {
            console.error(`Error polling job for ${moduleName}:`, err);
          }
        }
      }

      if (jobsChanged) {
        setUninstallJobs(updatedJobs);
      }
    };

    // Only poll if there are active jobs
    if (installJobs.size > 0 || uninstallJobs.size > 0) {
      pollInstallJobs();
      pollUninstallJobs();

      const interval = setInterval(() => {
        pollInstallJobs();
        pollUninstallJobs();
      }, 1500); // Poll every 1.5 seconds

      return () => clearInterval(interval);
    }
  }, [installJobs, uninstallJobs]);

  const fetchModulesAndExposures = async () => {
    try {
      setLoading(true);
      
      // Fetch modules
      const modulesResponse = await modulesApi.listModules();
      const modulesList = Array.isArray(modulesResponse.modules) ? modulesResponse.modules : [];
      setModules(modulesList);
      
      // Fetch exposures
      const exposuresResponse = await exposuresApi.listExposures();
      const exposuresList = exposuresResponse.exposures ?? [];
      setExposures(exposuresList);

      // Fetch links
      const linksResponse = await linksApi.listLinks();
      const linksList = linksResponse.links ?? [];
      setLinks(linksList);
      
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
    return exposures.find(exp => exp.moduleId === moduleId);
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
    const hasExposures = exposures.some(e => e.moduleId === moduleId);
    const hasLinks = links.some(link => link.modules && moduleId in link.modules);
    return hasExposures || hasLinks;
  };

  const getExposureUrl = (exposure: Exposure): string => {
    if (!exposure.protocol || !exposure.hostname || !exposure.containerPort) {
      return 'N/A';
    }
    return `${exposure.protocol}://${exposure.hostname}:${exposure.containerPort}`;
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
      setShowInstallDialog(false);
      setError(null);

      // Enqueue the install job
      const jobResponse = await jobsApi.enqueueInstall({
        queueEnqueueInstallRequest: {
          moduleId: moduleName,
          source: item.source || undefined,
        }
      });

      if (jobResponse.id) {
        // Store the job in our install jobs map
        setInstallJobs(prev => new Map(prev).set(moduleName, jobResponse));
      } else {
        throw new Error('No job ID returned from server');
      }
      
    } catch (err) {
      console.error('Install error:', err);
      setError(err instanceof Error ? err.message : 'Failed to enqueue install job');
    }
  };

  const handleUninstall = async (moduleName: string) => {
    if (!moduleName) {
      setError('Cannot uninstall: module name is missing');
      return;
    }

    // Check for exposures and links
    const moduleExposures = exposures.filter(e => e.moduleId === moduleName);
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
      setError(null);

      // Enqueue the uninstall job
      const jobResponse = await jobsApi.enqueueUninstall({
        queueEnqueueUninstallRequest: {
          moduleId: moduleName,
        }
      });

      if (jobResponse.id) {
        // Store the job in our uninstall jobs map
        setUninstallJobs(prev => new Map(prev).set(moduleName, jobResponse));
      } else {
        throw new Error('No job ID returned from server');
      }
      
    } catch (err) {
      console.error('Uninstall error:', err);
      setError(err instanceof Error ? err.message : 'Failed to enqueue uninstall job');
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

      {!loading && !error && modules.length === 0 && installJobs.size === 0 && (
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

      {!loading && !error && (installJobs.size > 0 || modules.length > 0) && (
        <div className="grid grid-2">
          {/* Show installing modules first */}
          {Array.from(installJobs.entries()).map(([moduleName, job]) => (
            <div key={`installing-${moduleName}`} className="card">
              <div className="module-header">
                <h3 className="module-name">{moduleName}</h3>
              </div>
              <InstallationProgress 
                moduleName={moduleName} 
                job={job}
                operationType="install"
                onCancel={job.status === 'queued' ? () => {
                  if (job.id) {
                    jobsApi.cancelJob({ id: job.id }).catch(err => 
                      console.error('Failed to cancel install job:', err)
                    );
                  }
                } : undefined}
              />
            </div>
          ))}

          {/* Show installed modules */}
          {modules.map((module, idx) => {
            const key = module.id || `module-${idx}`;
            const state = module.state || 'unknown';
            const ports = getModulePorts(module);
            const exposure = getModuleExposure(module.id);
            const isExposed = exposure ? 'Yes' : 'No';
            const linkedModules = getLinkedModules(module.id);
            const installJob = module.id ? installJobs.get(module.id) : undefined;
            const uninstallJob = module.id ? uninstallJobs.get(module.id) : undefined;
            const isUninstalling = !!uninstallJob;
            
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

                {uninstallJob && (
                  <InstallationProgress 
                    moduleName={module.id || 'Module'} 
                    job={uninstallJob}
                    operationType="uninstall"
                    onCancel={uninstallJob.status === 'queued' ? () => {
                      if (uninstallJob.id) {
                        jobsApi.cancelJob({ id: uninstallJob.id }).catch(err => 
                          console.error('Failed to cancel uninstall job:', err)
                        );
                      }
                    } : undefined}
                  />
                )}

                {!uninstallJob && (
                  <>
                    {module.containerName && (
                      <div className="module-detail">
                        <span className="detail-label">Container:</span>
                        <span className="detail-value">{module.containerName}</span>
                      </div>
                    )}

                    {module.ipAddress && (
                      <div className="module-detail">
                        <span className="detail-label">IP Address:</span>
                        <span className="detail-value">{module.ipAddress}</span>
                      </div>
                    )}

                    {module.gpuVendor && (
                      <div className="module-detail">
                        <span className="detail-label">GPU Vendor:</span>
                        <span className="detail-value">{module.gpuVendor}</span>
                      </div>
                    )}

                    {module.usingGpu && (
                      <div className="module-detail">
                        <span className="detail-label">GPU Usage:</span>
                        <span className="detail-value">Enabled</span>
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
                        disabled={isUninstalling || hasModuleDependencies(module.id)}
                        title={hasModuleDependencies(module.id) ? 'Module has exposures or links - remove them first' : ''}
                      >
                        {isUninstalling ? 'Uninstalling...' : 'Uninstall'}
                      </button>
                    </div>
                  </>
                )}
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
