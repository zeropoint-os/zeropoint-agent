import React, { useState, useEffect, useRef } from 'react';
import { ExposuresApi, ModulesApi, JobsApi, Configuration, ApiModule, ApiExposureResponse } from 'artifacts/clients/typescript';
import CreateExposureDialog from '../components/CreateExposureDialog';
import { LOADING_INDICATOR_DELAY } from '../constants';
import './Views.css';

type Exposure = ApiExposureResponse;

export default function ExposuresView() {
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [modules, setModules] = useState<ApiModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const loadingTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const exposuresApi = new ExposuresApi(new Configuration({ basePath: '/api' }));
  const modulesApi = new ModulesApi(new Configuration({ basePath: '/api' }));
  const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

  useEffect(() => {
    fetchExposures();
    // Refresh every 5 seconds
    const interval = setInterval(fetchExposures, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchExposures = async () => {
    loadingTimeoutRef.current = setTimeout(() => {
      setLoading(true);
    }, LOADING_INDICATOR_DELAY);

    try {
      const [exposuresRes, modulesRes] = await Promise.all([
        exposuresApi.listExposures(),
        modulesApi.listModules(),
      ]);

      setExposures(exposuresRes.exposures ?? []);
      setModules(Array.isArray(modulesRes.modules) ? modulesRes.modules : []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (loadingTimeoutRef.current) {
        clearTimeout(loadingTimeoutRef.current);
      }
      setLoading(false);
    }
  };

  const getModuleName = (moduleId: string | undefined): string => {
    if (!moduleId) return 'Unknown';
    const module = modules.find(m => m.id === moduleId);
    return module?.id || moduleId;
  };

  const getExposureUrl = (exposure: Exposure): string => {
    if (!exposure.protocol || !exposure.id) {
      return 'N/A';
    }
    // All exposures go through Envoy on port 80, mDNS is configured for the exposure ID
    return `${exposure.protocol}://${exposure.id}.local/`;
  };

  const handleDeleteExposure = async (exposureId: string) => {
    if (!window.confirm(`Delete exposure "${exposureId}"?`)) {
      return;
    }

    try {
      await jobsApi.enqueueDeleteExposure({
        queueEnqueueDeleteExposureRequest: {
          exposureId: exposureId,
        }
      });
      setError(null);
      setTimeout(() => fetchExposures(), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete exposure');
    }
  };

  return (
    <div className="view-container">
      {showCreateDialog && (
        <CreateExposureDialog
          isOpen={showCreateDialog}
          modules={modules}
          onClose={() => setShowCreateDialog(false)}
          onCreate={async () => {
            setShowCreateDialog(false);
            setTimeout(() => fetchExposures(), 1000);
          }}
        />
      )}

      {exposures.length > 0 && (
        <div className="view-header">
          <h1 className="section-title">Exposures</h1>
          <button
            className="button button-primary"
            onClick={() => setShowCreateDialog(true)}
          >
            <span>+</span> Create Exposure
          </button>
        </div>
      )}

      {exposures.length === 0 && (
        <h1 className="section-title">Exposures</h1>
      )}

      {error && (
        <div className="error-state">
          <p className="error-message">{error}</p>
          <button className="button button-secondary" onClick={() => setError(null)}>
            Dismiss
          </button>
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
          <h2>No exposures created</h2>
          <p>Create exposures to access your modules.</p>
          <button
            className="button button-primary"
            onClick={() => setShowCreateDialog(true)}
          >
            Create Exposure
          </button>
        </div>
      ) : (
        <div className="grid grid-2">
          {exposures.map((exposure, idx) => {
            const key = exposure.id || `exposure-${idx}`;
            return (
              <div key={key} className="card">
                <div style={{ marginBottom: '1rem' }}>
                  <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '1.125rem', fontWeight: '600' }}>
                    {exposure.id || 'Unnamed'}
                  </h3>
                  <p style={{ margin: '0', fontSize: '0.875rem', color: '#6b7280' }}>
                    Module: {getModuleName(exposure.moduleId)}
                  </p>
                </div>

                <div style={{ marginBottom: '1rem', padding: '0.75rem', backgroundColor: '#f0fdf4', borderRadius: '0.375rem' }}>
                  <p style={{ fontSize: '0.875rem', fontWeight: '500', color: '#15803d', marginBottom: '0.5rem' }}>
                    Access URL
                  </p>
                  <a
                    href={getExposureUrl(exposure)}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      fontSize: '0.875rem',
                      color: '#0369a1',
                      wordBreak: 'break-all',
                    }}
                  >
                    {getExposureUrl(exposure)} ↗
                  </a>
                </div>

                <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem', marginBottom: '1rem', color: '#6b7280' }}>
                  <div>
                    <strong>Protocol:</strong> {exposure.protocol}
                  </div>
                  <div>
                    <strong>Port:</strong> {exposure.containerPort}
                  </div>
                  {exposure.hostname && (
                    <div>
                      <strong>Host:</strong> {exposure.hostname}
                    </div>
                  )}
                </div>

                <button
                  className="button button-danger"
                  onClick={() => handleDeleteExposure(exposure.id || '')}
                  style={{ width: '100%' }}
                >
                  Delete
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
