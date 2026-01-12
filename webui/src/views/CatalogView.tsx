import React, { useState, useEffect } from 'react';
import './Views.css';

interface CatalogItem {
  name: string;
  description?: string;
  version?: string;
  tags?: string[];
}

export default function CatalogView() {
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCatalog();
  }, []);

  const fetchCatalog = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/catalogs');
      if (!response.ok) {
        throw new Error(`Failed to fetch catalog: ${response.statusText}`);
      }
      const data = await response.json();
      const items = Array.isArray(data) ? data : (data.items || data.catalogs || data.data || []);
      setCatalogItems(items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      setCatalogItems([]);
    } finally {
      setLoading(false);
    }
  };

  const handleInstall = (itemName: string) => {
    // TODO: Show install modal
    console.log('Install catalog item:', itemName);
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Catalog</h1>
        <button className="button button-secondary" onClick={fetchCatalog}>
          Refresh Catalog
        </button>
      </div>

      {loading && (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading catalog...</p>
        </div>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={fetchCatalog}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && catalogItems.length === 0 && (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
          </svg>
          <h2>Catalog is empty</h2>
          <p>No items available in the catalog.</p>
        </div>
      )}

      {!loading && !error && catalogItems.length > 0 && (
        <div className="grid grid-3">
          {catalogItems.map((item, idx) => {
            const key = item.name || `catalog-${idx}`;
            return (
              <div key={key} className="card">
                <h3 className="catalog-item-name">{item.name || 'Unnamed'}</h3>
                {item.version && <p className="catalog-item-version">v{item.version}</p>}
                {item.description && (
                  <p className="catalog-item-description">{item.description}</p>
                )}
                {item.tags && item.tags.length > 0 && (
                  <div className="catalog-item-tags">
                    {item.tags.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <button
                  className="button button-primary"
                  onClick={() => handleInstall(item.name || '')}
                >
                  Install
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
