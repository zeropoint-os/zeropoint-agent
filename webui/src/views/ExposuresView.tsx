import React, { useState, useEffect } from 'react';
import { ExposuresApi, ModulesApi, JobsApi, Configuration, ApiModule, ApiExposureResponse, QueueJobResponse } from 'artifacts/clients/typescript';
import CreateExposureDialog from '../components/CreateExposureDialog';
import InstallationProgress from '../components/InstallationProgress';
import './Views.css';

type Module = ApiModule;
type Exposure = ApiExposureResponse;

export default function ExposuresView() {
  const [exposures, setExposures] = useState<ApiExposureResponse[]>([]);
  const [modules, setModules] = useState<ApiModule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [createJobs, setCreateJobs] = useState<Map<string, QueueJobResponse>>(new Map());
  const [deleteJobs, setDeleteJobs] = useState<Map<string, QueueJobResponse>>(new Map());
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  useEffect(() => {
    fetchExposuresAndModules();
  }, []);

  useEffect(() => {
    const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

    const pollCreateJobs = async () => {
      const updatedJobs = new Map(createJobs);

      for (const [exposureId, job] of createJobs.entries()) {
        if (job.id && job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            
            if (updatedJob.status === 'completed' || updatedJob.status === 'failed' || updatedJob.status === 'cancelled') {
              // Remove completed/failed/cancelled jobs from the map
              updatedJobs.delete(exposureId);
              // Refresh exposures list when job completes
              if (updatedJob.status === 'completed') {
                await fetchExposuresAndModules();
              }
            } else {
              updatedJobs.set(exposureId, updatedJob);
            }
          } catch (err) {
            console.error('Error polling create job:', err);
          }
        }
      }

      setCreateJobs(updatedJobs);
    };

    const pollDeleteJobs = async () => {
      const updatedJobs = new Map(deleteJobs);

      for (const [exposureId, job] of deleteJobs.entries()) {
        if (job.id && job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            
            if (updatedJob.status === 'completed' || updatedJob.status === 'failed' || updatedJob.status === 'cancelled') {
              // Remove completed/failed/cancelled jobs from the map
              updatedJobs.delete(exposureId);
              // Refresh exposures list when job completes
              if (updatedJob.status === 'completed') {
                await fetchExposuresAndModules();
              }
            } else {
              updatedJobs.set(exposureId, updatedJob);
            }
          } catch (err) {
            console.error('Error polling delete job:', err);
          }
        }
      }

      setDeleteJobs(updatedJobs);
    };

    if (createJobs.size > 0 || deleteJobs.size > 0) {
      const interval = setInterval(() => {
        pollCreateJobs();
        pollDeleteJobs();
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [createJobs, deleteJobs]);

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
      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      
      // Generate an exposure ID from module and hostname
      const exposureId = `${data.module_id}-${data.hostname}`;
      
      const jobResponse = await jobsApi.enqueueCreateExposure({
        queueEnqueueCreateExposureRequest: {
          exposureId: exposureId,
          moduleId: data.module_id,
          hostname: data.hostname,
          protocol: data.protocol,
          containerPort: data.container_port,
        },
      });

      setCreateJobs(prev => new Map(prev).set(exposureId, jobResponse));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create exposure');
      throw err;
    }
  };

  const handleDeleteExposure = async (exposureId: string) => {
    if (!window.confirm(`Are you sure you want to delete exposure "${exposureId}"?`)) {
      return;
    }

    try {
      setError(null);

      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      const jobResponse = await jobsApi.enqueueDeleteExposure({
        queueEnqueueDeleteExposureRequest: {
          exposureId: exposureId,
        },
      });

      setDeleteJobs(prev => new Map(prev).set(exposureId, jobResponse));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete exposure');
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

      {!loading && (exposures.length > 0 || createJobs.size > 0) && (
        <div className="view-header">
          <h1 className="section-title">Exposures</h1>
          <button className="button button-primary" onClick={handleCreateExposure}>
            <span>+</span> Create Exposure
          </button>
        </div>
      )}
      {!loading && exposures.length === 0 && createJobs.size === 0 && (
        <div className="view-header">
          <h1 className="section-title">Exposures</h1>
        </div>
      )}
      {error && !loading && (
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
      ) : exposures.length === 0 && createJobs.size === 0 ? (
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
          {Array.from(createJobs.entries()).map(([exposureId, job]) => (
            <div key={`create-${exposureId}`} className="card">
              <InstallationProgress 
                moduleName={exposureId} 
                job={job}
                operationType="create_exposure"
                onCancel={job.status === 'queued' ? () => {
                  if (job.id) {
                    const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
                    jobsApi.cancelJob({ id: job.id }).catch(err => 
                      console.error('Failed to cancel create exposure job:', err)
                    );
                  }
                } : undefined}
              />
            </div>
          ))}
          {exposures.map((exposure, idx) => {
            const exposureId = exposure.id || `exposure-${idx}`;
            const url = exposure.hostname && exposure.containerPort 
              ? `${exposure.protocol || 'http'}://${exposure.hostname}.local:${exposure.containerPort}`
              : 'N/A';
            const createdDate = exposure.createdAt 
              ? new Date(exposure.createdAt).toLocaleDateString()
              : 'N/A';
            const deleteJob = deleteJobs.get(exposureId);
            const isDeleting = !!deleteJob;

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

                {deleteJob && (
                  <InstallationProgress 
                    moduleName={exposureId} 
                    job={deleteJob}
                    operationType="delete_exposure"
                    onCancel={deleteJob.status === 'queued' ? () => {
                      if (deleteJob.id) {
                        const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
                        jobsApi.cancelJob({ id: deleteJob.id }).catch(err => 
                          console.error('Failed to cancel delete exposure job:', err)
                        );
                      }
                    } : undefined}
                  />
                )}

                {!isDeleting && (
                  <>
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
                        disabled={isDeleting}
                      >
                        Delete
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
