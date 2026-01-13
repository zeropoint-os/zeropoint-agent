import React, { useState, useEffect } from 'react';
import BundleBrowser from '../components/BundleBrowser';
import './Views.css';

interface Bundle {
  name?: string;
  description?: string;
  modules?: string[];
  links?: { [key: string]: any };
  exposures?: { [key: string]: any };
  tags?: string[];
  [key: string]: any;
}

interface Exposure {
  id?: string;
  tags?: string[];
  [key: string]: any;
}

interface Link {
  id?: string;
  tags?: string[];
  [key: string]: any;
}

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
      // Fetch all exposures and links to find installed bundles (tagged components)
      const [exposuresRes, linksRes] = await Promise.all([
        fetch('/api/exposures'),
        fetch('/api/links'),
      ]);

      const exposuresData = exposuresRes.ok ? await exposuresRes.json() : { exposures: [] };
      const linksData = linksRes.ok ? await linksRes.json() : { links: [] };

      const exposures = Array.isArray(exposuresData.exposures) ? exposuresData.exposures : [];
      const links = Array.isArray(linksData.links) ? linksData.links : [];

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
      const bundleResponse = await fetch(`/api/catalogs/bundles/${bundleName}`);
      if (!bundleResponse.ok) {
        throw new Error(`Failed to fetch bundle: ${bundleResponse.statusText}`);
      }
      const bundle = await bundleResponse.json();

      // Step 1: Install modules
      if (bundle.modules && bundle.modules.length > 0) {
        for (const moduleName of bundle.modules) {
          setProgressMessage(`Installing module: ${moduleName}...`);
          const moduleResponse = await fetch(`/api/modules/${moduleName}`, {
            method: 'POST',
          });
          if (!moduleResponse.ok) {
            throw new Error(`Failed to install module ${moduleName}: ${moduleResponse.statusText}`);
          }
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

          const linkResponse = await fetch(`/api/links/${linkName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              modules: modules,
              tags: [bundleName], // Tag with bundle name
            }),
          });
          if (!linkResponse.ok) {
            throw new Error(`Failed to create link ${linkName}: ${linkResponse.statusText}`);
          }
        }
      }

      // Step 3: Create exposures with bundle tag
      if (bundle.exposures && Object.keys(bundle.exposures).length > 0) {
        for (const [exposureName, exposureDef] of Object.entries(bundle.exposures)) {
          setProgressMessage(`Creating exposure: ${exposureName}...`);
          const exposure = exposureDef as any;
          const exposureResponse = await fetch(`/api/exposures/${exposureName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              module_id: exposure.module,
              protocol: exposure.protocol,
              container_port: exposure.module_port,
              tags: [bundleName], // Tag with bundle name
            }),
          });
          if (!exposureResponse.ok) {
            throw new Error(`Failed to create exposure ${exposureName}: ${exposureResponse.statusText}`);
          }
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

      // Fetch all components to find tagged items
      const [exposuresRes, linksRes] = await Promise.all([
        fetch('/api/exposures'),
        fetch('/api/links'),
      ]);

      if (!exposuresRes.ok || !linksRes.ok) {
        throw new Error('Failed to fetch bundle components');
      }

      const exposuresData = await exposuresRes.json();
      const linksData = await linksRes.json();

      const exposures = Array.isArray(exposuresData.exposures) ? exposuresData.exposures : [];
      const links = Array.isArray(linksData.links) ? linksData.links : [];

      // Delete exposures tagged with this bundle
      const bundleExposures = exposures.filter((exp: any) => 
        exp.tags && exp.tags.includes(bundleName)
      );
      for (const exposure of bundleExposures) {
        setProgressMessage(`Removing exposure: ${exposure.id}...`);
        const deleteRes = await fetch(`/api/exposures/${exposure.id}`, {
          method: 'DELETE',
        });
        if (!deleteRes.ok) {
          throw new Error(`Failed to delete exposure ${exposure.id}`);
        }
      }

      // Delete links tagged with this bundle
      const bundleLinks = links.filter((link: any) => 
        link.tags && link.tags.includes(bundleName)
      );
      for (const link of bundleLinks) {
        setProgressMessage(`Removing link: ${link.id}...`);
        const deleteRes = await fetch(`/api/links/${link.id}`, {
          method: 'DELETE',
        });
        if (!deleteRes.ok) {
          throw new Error(`Failed to delete link ${link.id}`);
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
