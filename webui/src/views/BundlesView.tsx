import React, { useState, useEffect, useRef } from 'react';
import { BundlesApi, JobsApi, Configuration, ApiBundleResponse } from 'artifacts/clients/typescript';
import CatalogBrowser from '../components/CatalogBrowser';
import type { CatalogBundleResponse } from 'artifacts/clients/typescript';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

interface Bundle {
  id: string;
  name: string;
  description?: string;
  modules: string[];
  links?: Record<string, any>;
  exposures?: Record<string, any>;
  status: string;
}

export default function BundlesView() {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [uninstalling, setUninstalling] = useState<string | null>(null);
  const [showInstallDialog, setShowInstallDialog] = useState(false);
  const loadingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const bundlesApi = new BundlesApi(new Configuration({ basePath: '/api' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchBundles();
    // Refresh every 5 seconds
    const interval = setInterval(fetchBundles, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchBundles = async () => {
    loadingTimeoutRef.current = setTimeout(() => {
      setLoading(true);
    }, LOADING_INDICATOR_DELAY);

    try {
      const response = await bundlesApi.listBundles({});
      const bundles: Bundle[] = (response || []).map((apiBundle: ApiBundleResponse) => ({
        id: apiBundle.id || '',
        name: apiBundle.name || apiBundle.id || '',
        description: apiBundle.description,
        modules: apiBundle.modules || [],
        links: apiBundle.links,
        exposures: apiBundle.exposures,
        status: apiBundle.status || 'unknown',
      }));
      setBundles(bundles);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setBundles([]);
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
      }
      setLoading(false);
    }
  };

  const handleInstallBundle = async (bundleItem: CatalogBundleResponse) => {
    const bundleName = bundleItem.name || '';
    if (!window.confirm(`Install bundle "${bundleName}"?`)) {
      return;
    }

    try {
      setInstalling(bundleName);
      setError(null);

      // Enqueue the bundle installation job
      const job = await jobsApi.enqueueBundleInstall({
        queueEnqueueBundleInstallRequest: {
          bundleName: bundleName,
        },
      });

      // Poll for job completion
      let completed = false;
      let failed = false;
      while (!completed && !failed) {
        await new Promise(resolve => setTimeout(resolve, 500));
        const jobStatus = await jobsApi.getJob({ id: job.id || '' });
        
        if (jobStatus.status === 'completed') {
          completed = true;
          setInstalling(null);
          setShowInstallDialog(false);
          await fetchBundles();
        } else if (jobStatus.status === 'failed') {
          failed = true;
          setInstalling(null);
          setError(jobStatus.error || 'Installation failed');
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to install bundle');
      setInstalling(null);
    }
  };

  const handleUninstallBundle = async (bundleId: string) => {
    if (!window.confirm(`Uninstall bundle "${bundleId}"?`)) {
      return;
    }

    try {
      setError(null);
      setUninstalling(bundleId);

      // Enqueue the bundle uninstallation job
      const job = await jobsApi.enqueueBundleUninstall({
        queueEnqueueBundleUninstallRequest: {
          bundleId: bundleId,
        },
      });

      // Poll for job completion
      let completed = false;
      let failed = false;
      while (!completed && !failed) {
        await new Promise(resolve => setTimeout(resolve, 500));
        const jobStatus = await jobsApi.getJob({ id: job.id || '' });
        
        if (jobStatus.status === 'completed') {
          completed = true;
          setUninstalling(null);
          await fetchBundles();
        } else if (jobStatus.status === 'failed') {
          failed = true;
          setUninstalling(null);
          setError(jobStatus.error || 'Uninstallation failed');
        }
      }
    } catch (err) {
      setUninstalling(null);
      setError(err instanceof Error ? err.message : 'Failed to uninstall bundle');
    }
  };

  if (loading) {
    return (
      <div className="view-container">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading bundles...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="view-container">
      {showInstallDialog && (
        <CatalogBrowser
          filterType="bundles"
          onSelect={(item) => {
            setShowInstallDialog(false);
            handleInstallBundle(item as CatalogBundleResponse);
          }}
          onClose={() => setShowInstallDialog(false)}
        />
      )}

      {bundles.length > 0 && (
        <div className="view-header">
          <h1 className="section-title">Bundles</h1>
          <button
            className="button button-primary"
            onClick={() => setShowInstallDialog(true)}
          >
            <span>+</span> Install Bundle
          </button>
        </div>
      )}

      {bundles.length === 0 && (
        <h1 className="section-title">Bundles</h1>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {bundles.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6.2 2h11.6c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H6.2c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2z"></path>
            <path d="M12 10v6M9 13h6"></path>
          </svg>
          <h2>No bundles installed</h2>
          <p>Install bundles to quickly set up pre-configured environments.</p>
          <button
            className="button button-primary"
            onClick={() => setShowInstallDialog(true)}
          >
            Browse Bundles
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {bundles.map((bundle) => (
            <div key={bundle.id} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ flex: 1 }}>
                <div style={{ marginBottom: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.5rem' }}>
                    <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: '600' }}>
                      {bundle.name || bundle.id}
                    </h3>
                    <span style={{ fontSize: '0.75rem', fontWeight: '600', padding: '0.25rem 0.75rem', borderRadius: '0.375rem', backgroundColor: bundle.status === 'completed' ? 'var(--color-success-light)' : bundle.status === 'failed' ? 'var(--color-danger-light)' : 'var(--color-border)', color: bundle.status === 'completed' ? 'var(--color-success-dark)' : bundle.status === 'failed' ? 'var(--color-danger-dark)' : 'var(--color-text-secondary)' }}>
                      {bundle.status}
                    </span>
                  </div>
                  {bundle.description && (
                    <p style={{ margin: '0', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                      {bundle.description}
                    </p>
                  )}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.75rem', marginBottom: '1rem', fontSize: '0.875rem' }}>
                  <div style={{ padding: '0.5rem', backgroundColor: 'var(--color-surface-alt)', borderRadius: '0.375rem', textAlign: 'center' }}>
                    <div style={{ fontWeight: '600', fontSize: '1.25rem' }}>{bundle.modules?.length || 0}</div>
                    <div style={{ color: 'var(--color-text-secondary)', fontSize: '0.75rem' }}>Modules</div>
                  </div>
                  <div style={{ padding: '0.5rem', backgroundColor: 'var(--color-surface-alt)', borderRadius: '0.375rem', textAlign: 'center' }}>
                    <div style={{ fontWeight: '600', fontSize: '1.25rem' }}>{Object.keys(bundle.links || {}).length}</div>
                    <div style={{ color: 'var(--color-text-secondary)', fontSize: '0.75rem' }}>Links</div>
                  </div>
                  <div style={{ padding: '0.5rem', backgroundColor: 'var(--color-surface-alt)', borderRadius: '0.375rem', textAlign: 'center' }}>
                    <div style={{ fontWeight: '600', fontSize: '1.25rem' }}>{Object.keys(bundle.exposures || {}).length}</div>
                    <div style={{ color: 'var(--color-text-secondary)', fontSize: '0.75rem' }}>Exposures</div>
                  </div>
                </div>

                {bundle.modules && bundle.modules.length > 0 && (
                  <div style={{ marginBottom: '0.75rem' }}>
                    <p style={{ fontSize: '0.875rem', fontWeight: '500', marginBottom: '0.25rem' }}>Modules:</p>
                    <ul style={{ margin: '0', paddingLeft: '1.5rem', fontSize: '0.875rem' }}>
                      {bundle.modules.map((mod) => (
                        <li key={mod}>{mod}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <button
                className="button button-danger"
                onClick={() => handleUninstallBundle(bundle.id)}
                disabled={installing === bundle.id || uninstalling === bundle.id}
                style={{ width: '100%' }}
              >
                {uninstalling === bundle.id ? 'Uninstalling...' : 'Uninstall'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
