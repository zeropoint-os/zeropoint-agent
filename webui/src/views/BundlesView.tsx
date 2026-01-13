import React, { useState, useEffect } from 'react';
import BundleBrowser from '../components/BundleBrowser';
import { CatalogApi, ExposuresApi, LinksApi, ModulesApi, Configuration, ApiExposureResponse, ApiLink } from 'artifacts/clients/typescript';
import type { CatalogBundleResponse } from 'artifacts/clients/typescript';
import './Views.css';

type Bundle = CatalogBundleResponse;
type Exposure = ApiExposureResponse;
type Link = ApiLink;

export default function BundlesView() {
  const [installedBundles, setInstalledBundles] = useState<Bundle[]>([]);
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
      
      // Fetch all exposures and links to find installed bundles (tagged components)
      const [exposuresData, linksData] = await Promise.all([
        exposuresApi.exposuresGet(),
        linksApi.linksGet(),
      ]);

      const exposures = exposuresData.exposures ?? [];
      const links = linksData.links ?? [];

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

      // Create bundles from the tag names (these are installed bundles)
      const bundles: Bundle[] = Array.from(bundleNames).map(name => ({
        name: name,
      }));

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

      // Step 1: Install modules
      if (bundle.modules && bundle.modules.length > 0) {
        for (const moduleName of bundle.modules) {
          setProgressMessage(`Installing module: ${moduleName}...`);
          await modulesApi.modulesNamePost({ name: moduleName });
          // Wait for module to install
          await new Promise(resolve => setTimeout(resolve, 1000));
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
        for (const [exposureName, exposureDef] of Object.entries(bundle.exposures)) {
          setProgressMessage(`Creating exposure: ${exposureName}...`);
          const exposure = exposureDef as any;
          await exposuresApi.exposuresExposureIdPost({
            exposureId: exposureName,
            apiCreateExposureRequest: {
              moduleId: exposure.module,
              protocol: exposure.protocol,
              containerPort: exposure.module_port,
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

  const handleBundleSelected = (bundle: Bundle) => {
    if (bundle.name) {
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

      // Fetch all components to find tagged items
      const [exposuresData, linksData] = await Promise.all([
        exposuresApi.exposuresGet(),
        linksApi.linksGet(),
      ]);

      const exposures = exposuresData.exposures ?? [];
      const links = linksData.links ?? [];

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
