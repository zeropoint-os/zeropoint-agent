import React, { useState, useEffect } from 'react';
import { CatalogApi, Configuration } from 'artifacts/clients/typescript';
import type { CatalogModuleResponse, CatalogBundleResponse } from 'artifacts/clients/typescript';
import './CatalogBrowser.css';

export type CatalogItem = CatalogModuleResponse | CatalogBundleResponse;

interface CatalogBrowserProps {
  filterType?: string; // 'modules', 'bundles', etc. - if empty, show all
  onSelect: (item: CatalogItem) => void;
  onClose: () => void;
}

export default function CatalogBrowser({ filterType, onSelect, onClose }: CatalogBrowserProps) {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  // Initialize the API client
  const catalogApi = new CatalogApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    updateAndFetchCatalog();
  }, [filterType]);

  const updateCatalog = async () => {
    try {
      setUpdating(true);
      await catalogApi.updateCatalog();
    } catch (err) {
      console.error('Error updating catalog:', err);
      // Don't fail - continue with fetch anyway
    } finally {
      setUpdating(false);
    }
  };

  const updateAndFetchCatalog = async () => {
    await updateCatalog();
    await fetchCatalog();
  };

  const fetchCatalog = async () => {
    try {
      setLoading(true);
      let catalogItems: CatalogItem[] = [];

      if (filterType === 'bundles') {
        const bundles = await catalogApi.listCatalogBundles({});
        catalogItems = bundles || [];
      } else {
        // Default to modules
        const modules = await catalogApi.listCatalogModules({});
        catalogItems = modules || [];
      }

      setItems(catalogItems);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  const filteredItems = items.filter((item) => {
    const searchLower = searchTerm.toLowerCase();
    return (
      (item.name?.toLowerCase().includes(searchLower) || false) ||
      (item.description?.toLowerCase().includes(searchLower) || false)
    );
  });

  return (
    <div className="catalog-browser-overlay">
      <div className="catalog-browser">
        <div className="catalog-browser-header">
          <h2>Select {filterType ? filterType.slice(0, -1) : 'Item'} to Install</h2>
          <button className="catalog-browser-close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="catalog-browser-search">
          <input
            type="text"
            placeholder="Search catalog..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="catalog-search-input"
            disabled={loading || updating}
          />
          <button
            className="button button-secondary"
            onClick={fetchCatalog}
            disabled={loading || updating}
          >
            {updating ? 'Updating...' : 'Refresh'}
          </button>
        </div>

        {(loading || updating) && (
          <div className="catalog-browser-loading">
            <div className="spinner"></div>
            <p>{updating ? 'Updating catalog...' : 'Loading catalog...'}</p>
          </div>
        )}

        {error && (
          <div className="catalog-browser-error">
            <p className="error-message">{error}</p>
            <button className="button button-secondary" onClick={updateAndFetchCatalog}>
              Retry
            </button>
          </div>
        )}

        {!loading && !updating && !error && filteredItems.length === 0 && (
          <div className="catalog-browser-empty">
            <p>
              {searchTerm
                ? 'No items found matching your search.'
                : 'No items available in the catalog.'}
            </p>
          </div>
        )}

        {!loading && !updating && !error && filteredItems.length > 0 && (
          <div className="catalog-browser-list">
            {filteredItems.map((item, idx) => {
              const key = item.name || `item-${idx}`;
              return (
                <div key={key} className="catalog-browser-item">
                  <div className="catalog-item-info">
                    <h3 className="catalog-item-name">{item.name || 'Unnamed'}</h3>
                    {item.description && (
                      <p className="catalog-item-description">{item.description}</p>
                    )}
                  </div>
                  <button
                    className="button button-primary"
                    onClick={() => onSelect(item)}
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
