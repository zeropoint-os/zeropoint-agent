import React, { useState, useEffect } from 'react';
import { ExposuresApi, ModulesApi, Configuration, ApiModule, ApiExposureResponse } from 'artifacts/clients/typescript';
import CreateExposureDialog from '../components/CreateExposureDialog';
import './Views.css';

type Module = ApiModule;
type Exposure = ApiExposureResponse;

export default function ExposuresView() {
  const [exposures, setExposures] = useState<ApiExposureResponse[]>([]);
  const [modules, setModules] = useState<ApiModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [deletingExposure, setDeletingExposure] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  useEffect(() => {
    fetchExposuresAndModules();
  }, []);

  const fetchExposuresAndModules = async () => {
    try {
      setLoading(true);
      
      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
      
      // Fetch exposures
      const exposuresResponse = await exposuresApi.listExposures();
      const exposureList = exposuresResponse.exposures ?? [];
      setExposures(exposureList);

      // Fetch modules
      const modulesResponse = await modulesApi.listModules();
      const modulesList = modulesResponse.modules ?? [];
      setModules(modulesList);

      setError(null);
    } catch (err) {
      console.error('Error loading data:', err);
      setError(err instanceof Error ? err.message : 'Unknown error');
      setExposures([]);
      setModules([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateExposure = () => {
    setShowCreateDialog(true);
  };

  const handleCreateExposureSubmit = async (data: {
    module_id: string;
    hostname: string;
    protocol: string;
    container_port: number;
  }) => {
    try {
      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      await exposuresApi.createExposure({
        exposureId: data.module_id,
        apiCreateExposureRequest: {
          moduleId: data.module_id,
          hostname: data.hostname,
          protocol: data.protocol,
          containerPort: data.container_port,
        },
      });

      setSuccessMessage(`Exposure for "${data.module_id}" created successfully`);
      setTimeout(() => setSuccessMessage(null), 4000);

      // Refresh exposures list
      await fetchExposuresAndModules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create exposure');
      throw err; // Re-throw to let dialog handle it
    }
  };

  const handleDeleteExposure = async (exposureId: string) => {
    if (!window.confirm(`Are you sure you want to delete exposure "${exposureId}"?`)) {
      return;
    }

    try {
      setDeletingExposure(exposureId);
      setError(null);

      const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
      await exposuresApi.deleteExposure({ exposureId });

      setSuccessMessage(`Exposure "${exposureId}" deleted successfully`);
      setTimeout(() => setSuccessMessage(null), 4000);
      
      // Refresh exposures list
      await fetchExposuresAndModules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete exposure');
    } finally {
      setDeletingExposure(null);
    }
  };

  return (
    <div className="view-container">
      <CreateExposureDialog
        isOpen={showCreateDialog}
        modules={modules}
        onClose={() => setShowCreateDialog(false)}
        onCreate={handleCreateExposureSubmit}
      />

      {!loading && exposures.length > 0 && (
        <div className="view-header">
          <h1 className="section-title">Exposures</h1>
          <button className="button button-primary" onClick={handleCreateExposure}>
            <span>+</span> Create Exposure
          </button>
        </div>
      )}
      {(loading || exposures.length === 0) && (
        <div className="view-header">
          <h1 className="section-title">Exposures</h1>
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
            const url = exposure.hostname && exposure.containerPort 
              ? `${exposure.protocol || 'http'}://${exposure.hostname}.local:${exposure.containerPort}`
              : 'N/A';
            const createdDate = exposure.createdAt 
              ? new Date(exposure.createdAt).toLocaleDateString()
              : 'N/A';
            return (
              <div key={exposureId} className="card">
                <div className="exposure-header">
                  <div>
                    <h3 className="exposure-module">{exposure.moduleId || 'Unknown'}</h3>
                    <p className="exposure-id">{exposure.id}</p>
                  </div>
                  <span className={`status-badge status-${(exposure.status || 'unknown').toLowerCase()}`}>
                    {exposure.status || 'unknown'}
                  </span>
                </div>
                
                <div className="exposure-detail">
                  <span className="detail-label">URL:</span>
                  {url !== 'N/A' ? (
                    <a href={url} target="_blank" rel="noopener noreferrer" className="detail-link">
                      {url}
                    </a>
                  ) : (
                    <span className="detail-value">{url}</span>
                  )}
                </div>

                <div className="exposure-detail">
                  <span className="detail-label">Protocol:</span>
                  <span className="detail-value">{exposure.protocol || 'N/A'}</span>
                </div>

                <div className="exposure-detail">
                  <span className="detail-label">Port:</span>
                  <span className="detail-value">{exposure.containerPort || 'N/A'}</span>
                </div>

                <div className="exposure-detail">
                  <span className="detail-label">Created:</span>
                  <span className="detail-value">{createdDate}</span>
                </div>

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
                  <button
                    className="button button-danger"
                    onClick={() => handleDeleteExposure(exposureId)}
                    disabled={deletingExposure === exposureId}
                  >
                    {deletingExposure === exposureId ? 'Deleting...' : 'Delete'}
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
