import React, { useState, useEffect } from 'react';
import BundleBrowser from '../components/BundleBrowser';
import { CatalogApi, ExposuresApi, LinksApi, ModulesApi, JobsApi, Configuration, ApiExposureResponse, ApiLink, QueueJobResponse } from 'artifacts/clients/typescript';
import type { CatalogBundleResponse } from 'artifacts/clients/typescript';
import JobProgressCard from '../components/JobProgressCard';
import './Views.css';

type Bundle = CatalogBundleResponse;
type Exposure = ApiExposureResponse;
type Link = ApiLink;

interface BundleDetails {
  name: string;
  modules: string[];
  links: string[];
  exposures: string[];
}

export default function BundlesView() {
  const [installedBundles, setInstalledBundles] = useState<BundleDetails[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installingBundle, setInstallingBundle] = useState<string | null>(null);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [showBundleBrowser, setShowBundleBrowser] = useState(false);
  const [bundleJobs, setBundleJobs] = useState<Map<string, QueueJobResponse>>(new Map());

  useEffect(() => {
    fetchInstalledBundles();
  }, []);

  const fetchInstalledBundles = async () => {
    try {
      setLoading(true);
      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));

      // Fetch all exposures, links, and modules to find installed bundles
      const [exposuresData, linksData, modulesData] = await Promise.all([
        exposuresApi.listExposures(),
        linksApi.listLinks(),
        modulesApi.listModules(),
      ]);

      const exposures = exposuresData.exposures ?? [];
      const links = linksData.links ?? [];
      const modules = modulesData.modules ?? [];

      // Collect all unique bundle names from tags
      const bundleNames = new Set<string>();

      exposures.forEach((exp: Exposure) => {
        if (exp.tags) {
          exp.tags.forEach(tag => bundleNames.add(tag));
        }
      });

      links.forEach((link: Link) => {
        if (link.tags) {
          link.tags.forEach(tag => bundleNames.add(tag));
        }
      });

      // Create bundles with details
      const bundles: BundleDetails[] = Array.from(bundleNames).map(name => {
        const bundleExposures = exposures.filter(exp => exp.tags?.includes(name));
        const bundleLinks = links.filter(link => link.tags?.includes(name));
        const bundleModules = new Set<string>();

        // Get module IDs from exposures
        bundleExposures.forEach(exp => {
          if (exp.moduleId) {
            bundleModules.add(exp.moduleId);
          }
        });

        // Get module IDs from links
        bundleLinks.forEach(link => {
          if (link.modules) {
            Object.keys(link.modules).forEach(moduleId => {
              bundleModules.add(moduleId);
            });
          }
        });

        return {
          name: name,
          modules: Array.from(bundleModules).sort(),
          links: bundleLinks.map(l => l.id || '').filter(Boolean).sort(),
          exposures: bundleExposures.map(e => e.id || '').filter(Boolean).sort(),
        };
      });

      setInstalledBundles(bundles);
      setError(null);
    } catch (err) {
      console.error('Error loading bundles:', err);
      setError(err instanceof Error ? err.message : 'Failed to load bundles');
      setInstalledBundles([]);
    } finally {
      setLoading(false);
    }
  };

  const handleInstallBundle = async (bundleName: string) => {
    if (!bundleName) {
      setError('Bundle name is missing');
      return;
    }

    if (!window.confirm(`Install bundle "${bundleName}"? This will install all included modules, create links, and set up exposures.`)) {
      return;
    }

    try {
      setInstallingBundle(bundleName);
      setError(null);
      setProgressMessage(null);

      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));
      const bundle = await catalogApi.getCatalogBundle({ bundleName });

      const newJobs = new Map(bundleJobs);
      const moduleJobIds: string[] = [];
      const linkJobIds: string[] = [];

      // Step 1: Enqueue module installation jobs
      if (bundle.modules && bundle.modules.length > 0) {
        for (const moduleName of bundle.modules) {
          setProgressMessage(`Queuing module installation: ${moduleName}...`);

          const moduleInfo = await catalogApi.getCatalogModule({ moduleName });

          const response = await jobsApi.enqueueInstall({
            queueEnqueueInstallRequest: {
              moduleId: moduleName,
              source: moduleInfo.source || '',
              tags: [bundleName]
            }
          });

          if (response.id) {
            moduleJobIds.push(response.id);
            newJobs.set(response.id, response);
          }
        }
      }

      // Step 2: Enqueue link creation jobs with module job dependencies
      if (bundle.links && Object.keys(bundle.links).length > 0) {
        for (const [linkName, linkDef] of Object.entries(bundle.links)) {
          setProgressMessage(`Queuing link creation: ${linkName}...`);
          const modules: { [key: string]: { [key: string]: any } } = {};

          // linkDef is an array of module bindings
          const linkArray = Array.isArray(linkDef) ? linkDef : [linkDef];
          for (const binding of linkArray) {
            if (binding.module && binding.bind) {
              modules[binding.module] = binding.bind;
            }
          }

          const response = await jobsApi.enqueueCreateLink({
            queueEnqueueCreateLinkRequest: {
              linkId: linkName,
              modules: modules,
              tags: [bundleName],
              dependsOn: moduleJobIds // Links depend on all module installations
            }
          });

          if (response.id) {
            linkJobIds.push(response.id);
            newJobs.set(response.id, response);
          }
        }
      }

      // Step 3: Enqueue exposure creation jobs with link job dependencies
      if (bundle.exposures && Object.keys(bundle.exposures).length > 0) {
        for (const [exposureName, exposureDef] of Object.entries(bundle.exposures)) {
          setProgressMessage(`Queuing exposure creation: ${exposureName}...`);
          const exposure = exposureDef as any;

          // Use the modulePort from the bundle definition
          const containerPort = exposure.modulePort;
          if (!containerPort) {
            throw new Error(`No modulePort defined for exposure ${exposureName} in bundle`);
          }

          // Generate hostname for HTTP exposures (required by API)
          let hostname: string | undefined;
          if (exposure.protocol === 'http') {
            hostname = exposureName;
          }

          const response = await jobsApi.enqueueCreateExposure({
            queueEnqueueCreateExposureRequest: {
              exposureId: exposureName,
              moduleId: exposure.module,
              protocol: exposure.protocol,
              containerPort: containerPort,
              hostname: hostname,
              tags: [bundleName],
              dependsOn: linkJobIds // Exposures depend on all link creations
            }
          });

          if (response.id) {
            newJobs.set(response.id, response);
          }
        }
      }

      setBundleJobs(newJobs);
      setProgressMessage(`${bundleName} installation queued. Jobs will execute in sequence.`);
      setInstallingBundle(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install bundle');
      setInstallingBundle(null);
    }
  };

  // Polling effect for bundle jobs
  useEffect(() => {
    if (bundleJobs.size === 0) return;

    const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
    let pollInterval: NodeJS.Timeout | null = null;
    let cleanupTimeout: NodeJS.Timeout | null = null;

    const startPolling = () => {
      pollInterval = setInterval(async () => {
        const updatedJobs = new Map(bundleJobs);
        let allDone = true;

        for (const [jobId, _] of updatedJobs) {
          try {
            const job = await jobsApi.getJob({ id: jobId });
            updatedJobs.set(jobId, job);

            if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
              // Job reached terminal status
            } else {
              allDone = false;
            }
          } catch (err) {
            // Job might have been removed
            updatedJobs.delete(jobId);
          }
        }

        setBundleJobs(updatedJobs);

        if (allDone || updatedJobs.size === 0) {
          if (pollInterval) clearInterval(pollInterval);
          // Refresh installed bundles list
          await fetchInstalledBundles();
          setTimeout(() => setProgressMessage(null), 2000);
        }
      }, 1000);

      // Clean up polling after 30 minutes
      cleanupTimeout = setTimeout(() => {
        if (pollInterval) clearInterval(pollInterval);
      }, 30 * 60 * 1000);
    };

    startPolling();

    return () => {
      if (pollInterval) clearInterval(pollInterval);
      if (cleanupTimeout) clearTimeout(cleanupTimeout);
    };
  }, [bundleJobs]);

  const handleBundleSelected = (bundle: CatalogBundleResponse) => {
    if (bundle.name) {
      setShowBundleBrowser(false);
      handleInstallBundle(bundle.name);
    }
  };

  const handleUninstallBundle = async (bundleName: string) => {
    if (!bundleName) {
      setError('Bundle name is missing');
      return;
    }

    if (!window.confirm(`Uninstall bundle "${bundleName}"? This will remove all links and exposures tagged with this bundle.`)) {
      return;
    }

    try {
      setInstallingBundle(bundleName);
      setError(null);

      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));

      // Fetch all components to find tagged items
      const [exposuresData, linksData, modulesData] = await Promise.all([
        exposuresApi.listExposures(),
        linksApi.listLinks(),
        modulesApi.listModules(),
      ]);

      const exposures = exposuresData.exposures ?? [];
      const links = linksData.links ?? [];
      const modules = modulesData.modules ?? [];

      const newJobs = new Map(bundleJobs);
      const exposureJobIds: string[] = [];
      const linkJobIds: string[] = [];

      // Step 1: Enqueue exposure deletion jobs
      const bundleExposures = exposures.filter((exp: any) =>
        exp.tags && exp.tags.includes(bundleName)
      );
      for (const exposure of bundleExposures) {
        setProgressMessage(`Queuing exposure removal: ${exposure.id}...`);
        const response = await jobsApi.enqueueDeleteExposure({
          queueEnqueueDeleteExposureRequest: {
            exposureId: exposure.id ?? ''
          }
        });

        if (response.id) {
          exposureJobIds.push(response.id);
          newJobs.set(response.id, response);
        }
      }

      // Step 2: Enqueue link deletion jobs (depend on exposure deletions)
      const bundleLinks = links.filter((link: any) =>
        link.tags && link.tags.includes(bundleName)
      );
      for (const link of bundleLinks) {
        setProgressMessage(`Queuing link removal: ${link.id}...`);
        const response = await jobsApi.enqueueDeleteLink({
          queueEnqueueDeleteLinkRequest: {
            linkId: link.id ?? '',
            dependsOn: exposureJobIds
          }
        });

        if (response.id) {
          linkJobIds.push(response.id);
          newJobs.set(response.id, response);
        }
      }

      // Step 3: Enqueue module deletion jobs (depend on link deletions)
      const bundleModules = modules.filter((mod: any) =>
        mod.tags && mod.tags.includes(bundleName)
      );
      for (const module of bundleModules) {
        setProgressMessage(`Queuing module removal: ${module.id}...`);
        const response = await jobsApi.enqueueUninstall({
          queueEnqueueUninstallRequest: {
            moduleId: module.id ?? '',
            dependsOn: linkJobIds
          }
        });

        if (response.id) {
          newJobs.set(response.id, response);
        }
      }

      setBundleJobs(newJobs);
      setProgressMessage(`${bundleName} uninstallation queued. Jobs will execute in sequence.`);
      setInstallingBundle(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to uninstall bundle');
      setInstallingBundle(null);
    }
  };

  return (
    <div className="view-container">
      <BundleBrowser
        isOpen={showBundleBrowser}
        onClose={() => setShowBundleBrowser(false)}
        onSelect={handleBundleSelected}
      />

      <div className="view-header">
        <h1 className="section-title">Bundles</h1>
        {installedBundles.length > 0 && (
          <button
            className="button button-primary"
            onClick={() => setShowBundleBrowser(true)}
          >
            <span>+</span> Add Bundle
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

      {progressMessage && (
        <div className="success-state">
          <p className="success-message">{progressMessage}</p>
        </div>
      )}

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading bundles...</p>
        </div>
      ) : installedBundles.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6.2 2h11.6c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H6.2c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2z"></path>
            <path d="M12 10v6M9 13h6"></path>
          </svg>
          <h2>No bundles installed</h2>
          <p>Install bundles to set up complete application stacks.</p>
          <button
            className="button button-primary"
            onClick={() => setShowBundleBrowser(true)}
          >
            Browse Bundles
          </button>
        </div>
      ) : (
        <>
          {/* Display job progress cards */}
          {bundleJobs.size > 0 && (
            <div className="grid grid-2" style={{ marginBottom: '2rem' }}>
              {Array.from(bundleJobs.values()).map((job) => (
                <JobProgressCard
                  key={job.id}
                  job={job}
                  onCancel={job.status === 'queued' ? () => {
                    if (job.id) {
                      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
                      jobsApi.cancelJob({ id: job.id }).catch(err => 
                        console.error('Failed to cancel job:', err)
                      );
                    }
                  } : undefined}
                />
              ))}
            </div>
          )}

          <div className="grid grid-2">
            {installedBundles.map((bundle, idx) => {
              const key = bundle.name || `bundle-${idx}`;
              return (
                <div key={key} className="card">
                  <div className="bundle-header">
                    <h3 className="bundle-name">{bundle.name || 'Unnamed Bundle'}</h3>
                  </div>

                  <div className="bundle-details">
                    {bundle.modules.length > 0 && (
                      <div className="bundle-section">
                        <p className="section-label">Modules ({bundle.modules.length})</p>
                        <ul className="bundle-list">
                          {bundle.modules.map(mod => (
                            <li key={mod}>{mod}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {bundle.exposures.length > 0 && (
                      <div className="bundle-section">
                        <p className="section-label">Exposures ({bundle.exposures.length})</p>
                        <ul className="bundle-list">
                          {bundle.exposures.map(exp => (
                            <li key={exp}>{exp}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {bundle.links.length > 0 && (
                      <div className="bundle-section">
                        <p className="section-label">Links ({bundle.links.length})</p>
                        <ul className="bundle-list">
                          {bundle.links.map(link => (
                            <li key={link}>{link}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>

                  <div className="bundle-actions">
                    <button
                      className="button button-danger"
                      onClick={() => handleUninstallBundle(bundle.name || '')}
                      disabled={installingBundle === bundle.name}
                    >
                      Uninstall
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
