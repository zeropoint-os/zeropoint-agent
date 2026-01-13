import React, { useState, useEffect } from 'react';
import BundleBrowser from '../components/BundleBrowser';
import { CatalogApi, ExposuresApi, LinksApi, ModulesApi, Configuration, ApiExposureResponse, ApiLink } from 'artifacts/clients/typescript';
import type { CatalogBundleResponse } from 'artifacts/clients/typescript';
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
        exposuresApi.exposuresGet(),
        linksApi.linksGet(),
        modulesApi.modulesGet(),
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

      // Fetch the bundle definition
      const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));
      const bundle = await catalogApi.catalogsBundlesBundleNameGet({ bundleName });

      const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));

      // Step 1: Install modules with streaming
      if (bundle.modules && bundle.modules.length > 0) {
        for (const moduleName of bundle.modules) {
          setProgressMessage(`Installing module: ${moduleName}...`);

          // Fetch module details from catalog to get source
          const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));
          const moduleInfo = await catalogApi.catalogsModulesModuleNameGet({ moduleName });

          // Use raw fetch to handle streaming response
          const response = await fetch(`/api/modules/${moduleName}`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({
              source: moduleInfo.source || '',
              module_id: moduleName,
              tags: [bundleName]
            })
          });

          if (!response.ok) {
            throw new Error(`Failed to install module ${moduleName}: ${response.statusText}`);
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
                    setProgressMessage(`Installing module: ${moduleName}... ${message}`);
                  } catch {
                    if (line.trim()) {
                      setProgressMessage(`Installing module: ${moduleName}... ${line}`);
                    }
                  }
                }
              }
            }
          }
        }
      }

      // Step 2: Create links with bundle tag
      if (bundle.links && Object.keys(bundle.links).length > 0) {
        for (const [linkName, linkDef] of Object.entries(bundle.links)) {
          setProgressMessage(`Creating link: ${linkName}...`);
          const modules: { [key: string]: { [key: string]: string } } = {};

          // linkDef is an array of module bindings
          const linkArray = Array.isArray(linkDef) ? linkDef : [linkDef];
          for (const binding of linkArray) {
            if (binding.module && binding.bind) {
              modules[binding.module] = binding.bind;
            }
          }

          await linksApi.linksIdPost({
            id: linkName,
            apiCreateLinkRequest: {
              modules: modules,
              tags: [bundleName], // Tag with bundle name
            },
          });
        }
      }

      // Step 3: Create exposures with bundle tag
      if (bundle.exposures && Object.keys(bundle.exposures).length > 0) {
        // Fetch modules to get port information
        const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
        const modulesResponse = await modulesApi.modulesGet();
        const installedModules = modulesResponse.modules ?? [];

        for (const [exposureName, exposureDef] of Object.entries(bundle.exposures)) {
          setProgressMessage(`Creating exposure: ${exposureName}...`);
          const exposure = exposureDef as any;

          // Find the installed module to get port information
          const module = installedModules.find(m => m.id === exposure.module);
          if (!module) {
            throw new Error(`Module ${exposure.module} not found when creating exposure`);
          }

          // Get the port from the module's first container's matching port
          let containerPort: number | undefined;
          if (module.containers) {
            for (const container of Object.values(module.containers)) {
              const containerObj = container as any;
              if (containerObj.ports) {
                for (const port of Object.values(containerObj.ports)) {
                  const portObj = port as any;
                  // Use the first port matching the exposure's protocol or just the first available port
                  if (!containerPort || (exposure.protocol && portObj.protocol === exposure.protocol)) {
                    containerPort = portObj.port;
                    if (exposure.protocol && portObj.protocol === exposure.protocol) {
                      break; // Found a matching protocol, use it
                    }
                  }
                }
                if (containerPort) break;
              }
            }
          }

          if (!containerPort) {
            throw new Error(`No port found for module ${exposure.module} when creating exposure`);
          }

          // Generate hostname for HTTP exposures (required by API)
          let hostname: string | undefined;
          if (exposure.protocol === 'http') {
            hostname = exposureName;
          }

          await exposuresApi.exposuresExposureIdPost({
            exposureId: exposureName,
            apiCreateExposureRequest: {
              moduleId: exposure.module,
              protocol: exposure.protocol,
              containerPort: containerPort,
              hostname: hostname,
              tags: [bundleName], // Tag with bundle name
            },
          });
        }
      }

      setProgressMessage(`${bundleName} installed successfully!`);
      setInstallingBundle(null);

      // Refresh installed bundles list
      await fetchInstalledBundles();
      setTimeout(() => setProgressMessage(null), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install bundle');
      setInstallingBundle(null);
    }
  };

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

      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));

      // Fetch all components to find tagged items
      const [exposuresData, linksData, modulesData] = await Promise.all([
        exposuresApi.exposuresGet(),
        linksApi.linksGet(),
        modulesApi.modulesGet(),
      ]);

      const exposures = exposuresData.exposures ?? [];
      const links = linksData.links ?? [];
      const modules = modulesData.modules ?? [];

      // Delete exposures tagged with this bundle
      const bundleExposures = exposures.filter((exp: any) =>
        exp.tags && exp.tags.includes(bundleName)
      );
      for (const exposure of bundleExposures) {
        setProgressMessage(`Removing exposure: ${exposure.id}...`);
        await exposuresApi.exposuresExposureIdDelete({ exposureId: exposure.id ?? '' });
      }

      // Delete links tagged with this bundle
      const bundleLinks = links.filter((link: any) =>
        link.tags && link.tags.includes(bundleName)
      );
      for (const link of bundleLinks) {
        setProgressMessage(`Removing link: ${link.id}...`);
        await linksApi.linksIdDelete({ id: link.id ?? '' });
      }

      // Delete modules tagged with this bundle
      const bundleModules = modules.filter((mod: any) =>
        mod.tags && mod.tags.includes(bundleName)
      );
      for (const module of bundleModules) {
        setProgressMessage(`Removing module: ${module.id}...`);
        const uninstallResponse = await fetch(`/api/modules/${module.id}`, {
          method: 'DELETE',
          headers: {
            'Content-Type': 'application/json'
          }
        });

        if (!uninstallResponse.ok) {
          throw new Error(`Failed to uninstall module ${module.id}: ${uninstallResponse.statusText}`);
        }

        // Process the streaming response
        const reader = uninstallResponse.body?.getReader();
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
                  setProgressMessage(`Removing module: ${module.id}... ${message}`);
                } catch {
                  if (line.trim()) {
                    setProgressMessage(`Removing module: ${module.id}... ${line}`);
                  }
                }
              }
            }
          }
        }
      }

      setProgressMessage(`${bundleName} uninstalled successfully!`);

      // Refresh installed bundles list
      await fetchInstalledBundles();
      setTimeout(() => setProgressMessage(null), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to uninstall bundle');
    } finally {
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
      )}
    </div>
  );
}
