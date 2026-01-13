import React, { useState, useEffect } from 'react';
import { CatalogApi, Configuration } from 'artifacts/clients/typescript';
import './BundleBrowser.css';

interface Bundle {
  name?: string;
  description?: string;
  modules?: string[];
  links?: { [key: string]: any };
  exposures?: { [key: string]: any };
  [key: string]: any;
}

interface BundleBrowserProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (bundle: Bundle) => void;
}

export default function BundleBrowser({ isOpen, onClose, onSelect }: BundleBrowserProps) {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    if (isOpen) {
      fetchBundles();
    }
  }, [isOpen]);

  const fetchBundles = async () => {
    try {
      setLoading(true);
      const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));
      const bundleList = await catalogApi.catalogsBundlesGet();
      setBundles(bundleList);
      setError(null);
    } catch (err) {
      console.error('Error loading bundles:', err);
      setError(err instanceof Error ? err.message : 'Failed to load bundles');
      setBundles([]);
    } finally {
      setLoading(false);
    }
  };

  const filteredBundles = bundles.filter((bundle) => {
    const searchLower = searchTerm.toLowerCase();
    return (
      (bundle.name?.toLowerCase().includes(searchLower) || false) ||
      (bundle.description?.toLowerCase().includes(searchLower) || false)
    );
  });

  if (!isOpen) return null;

  return (
    <div className="catalog-browser-overlay">
      <div className="catalog-browser">
        <div className="catalog-browser-header">
          <h2>Select bundle to install</h2>
          <button className="catalog-browser-close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="catalog-browser-search">
          <input
            type="text"
            placeholder="Search bundles..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="catalog-search-input"
          />
        </div>

        {loading && (
          <div className="catalog-browser-loading">
            <div className="spinner"></div>
            <p>Loading bundles...</p>
          </div>
        )}

        {error && (
          <div className="catalog-browser-error">
            <p className="error-message">{error}</p>
            <button className="button button-secondary" onClick={fetchBundles}>
              Retry
            </button>
          </div>
        )}

        {!loading && !error && filteredBundles.length === 0 && (
          <div className="catalog-browser-empty">
            <p>
              {searchTerm
                ? 'No bundles found matching your search.'
                : 'No bundles available in the catalog.'}
            </p>
          </div>
        )}

        {!loading && !error && filteredBundles.length > 0 && (
          <div className="catalog-browser-list">
            {filteredBundles.map((bundle, idx) => {
              const key = bundle.name || `bundle-${idx}`;
              return (
                <div key={key} className="catalog-browser-item">
                  <div className="catalog-item-info">
                    <h3 className="catalog-item-name">{bundle.name || 'Unnamed'}</h3>
                    {bundle.description && (
                      <p className="catalog-item-description">{bundle.description}</p>
                    )}
                    {bundle.modules && bundle.modules.length > 0 && (
                      <p className="catalog-item-modules">
                        <small>Includes: {bundle.modules.join(', ')}</small>
                      </p>
                    )}
                  </div>
                  <button
                    className="button button-primary"
                    onClick={() => onSelect(bundle)}
                  >
                    Select
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
