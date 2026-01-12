import React, { useState, useEffect } from 'react';
import './Views.css';

interface Exposure {
  id?: string;
  module?: string;
  port?: number;
  protocol?: string;
  tags?: string[];
  [key: string]: any;
}

export default function ExposuresView() {
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchExposures();
  }, []);

  const fetchExposures = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/exposures');
      if (!response.ok) {
        throw new Error(`Failed to fetch exposures: ${response.statusText}`);
      }
      const data = await response.json();
      const exposureList = Array.isArray(data) ? data : (data.exposures || data.data || []);
      setExposures(exposureList);
    } catch (err) {
      console.error('Error loading exposures:', err);
      setExposures([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateExposure = () => {
    // TODO: Show create exposure modal
    console.log('Create exposure');
  };

  const handleDeleteExposure = (exposureId: string) => {
    // TODO: Show delete confirmation
    console.log('Delete exposure:', exposureId);
  };

  return (
    <div className="view-container">
      <div className="view-header">
        <h1 className="section-title">Exposures</h1>
        <button className="button button-primary" onClick={handleCreateExposure}>
          <span>+</span> Create Exposure
        </button>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading exposures...</p>
        </div>
      ) : exposures.length === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
            <circle cx="12" cy="12" r="3"></circle>
          </svg>
          <h2>No exposures</h2>
          <p>Expose modules to make them accessible from outside.</p>
          <button className="button button-primary" onClick={handleCreateExposure}>
            Create Exposure
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {exposures.map((exposure, idx) => {
            const exposureId = exposure.id || `exposure-${idx}`;
            return (
              <div key={exposureId} className="card">
                <div className="exposure-header">
                  <h3 className="exposure-module">{exposure.module || 'Unknown'}</h3>
                  <span className="badge badge-info">{exposure.protocol || 'N/A'}</span>
                </div>
                <p className="exposure-port">Port: {exposure.port || 'N/A'}</p>
                {exposure.tags && exposure.tags.length > 0 && (
                  <div className="exposure-tags">
                    {exposure.tags.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="exposure-actions">
                  <button className="button button-secondary">View</button>
                  <button
                    className="button button-danger"
                    onClick={() => handleDeleteExposure(exposureId)}
                  >
                    Delete
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
