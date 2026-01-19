import React, { useState, useEffect } from 'react';
import { LinksApi, JobsApi, Configuration, ApiLink, QueueJobResponse } from 'artifacts/clients/typescript';
import CreateLinkDialog from '../components/CreateLinkDialog';
import InstallationProgress from '../components/InstallationProgress';
import './Views.css';

type Link = ApiLink;

export default function LinksView() {
  const [links, setLinks] = useState<ApiLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createJobs, setCreateJobs] = useState<Map<string, QueueJobResponse>>(new Map());
  const [deleteJobs, setDeleteJobs] = useState<Map<string, QueueJobResponse>>(new Map());
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  useEffect(() => {
    fetchLinks();
  }, []);

  useEffect(() => {
    const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));

    const pollCreateJobs = async () => {
      const updatedJobs = new Map(createJobs);

      for (const [linkName, job] of createJobs.entries()) {
        if (job.id && job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            
            if (updatedJob.status === 'completed' || updatedJob.status === 'failed' || updatedJob.status === 'cancelled') {
              // Remove completed/failed/cancelled jobs from the map
              updatedJobs.delete(linkName);
              // Refresh links list when job completes
              if (updatedJob.status === 'completed') {
                await fetchLinks();
              }
            } else {
              updatedJobs.set(linkName, updatedJob);
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

      for (const [linkName, job] of deleteJobs.entries()) {
        if (job.id && job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled') {
          try {
            const updatedJob = await jobsApi.getJob({ id: job.id });
            
            if (updatedJob.status === 'completed' || updatedJob.status === 'failed' || updatedJob.status === 'cancelled') {
              // Remove completed/failed/cancelled jobs from the map
              updatedJobs.delete(linkName);
              // Refresh links list when job completes
              if (updatedJob.status === 'completed') {
                await fetchLinks();
              }
            } else {
              updatedJobs.set(linkName, updatedJob);
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

  const fetchLinks = async () => {
    try {
      setLoading(true);
      const linksApi = new LinksApi(new Configuration({ basePath: '/api' }));
      const response = await linksApi.listLinks();
      const linkList = response.links ?? [];
      setLinks(linkList);
      setError(null);
    } catch (err) {
      console.error('Error loading links:', err);
      setError(err instanceof Error ? err.message : 'Unknown error');
      setLinks([]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateLink = () => {
    setShowCreateDialog(true);
  };

  const handleCreateLinkSubmit = async (data: {
    id: string;
    modules: { [moduleId: string]: { [key: string]: string } };
  }) => {
    try {
      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      
      const jobResponse = await jobsApi.enqueueCreateLink({
        queueEnqueueCreateLinkRequest: {
          linkId: data.id,
          modules: data.modules,
        },
      });

      setCreateJobs(prev => new Map(prev).set(data.id, jobResponse));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create link');
      throw err;
    }
  };

  const handleDeleteLink = async (linkId: string) => {
    if (!window.confirm(`Are you sure you want to delete link "${linkId}"?`)) {
      return;
    }

    try {
      setError(null);

      const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
      const jobResponse = await jobsApi.enqueueDeleteLink({
        queueEnqueueDeleteLinkRequest: {
          linkId: linkId,
        },
      });

      setDeleteJobs(prev => new Map(prev).set(linkId, jobResponse));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete link');
    }
  };

  return (
    <div className="view-container">
      <CreateLinkDialog
        isOpen={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreate={handleCreateLinkSubmit}
      />

      {(links.length > 0 || createJobs.size > 0) && (
        <div className="view-header">
          <h1 className="section-title">Links</h1>
          <button className="button button-primary" onClick={handleCreateLink}>
            <span>+</span> Create Link
          </button>
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

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading links...</p>
        </div>
      ) : links.length === 0 && createJobs.size === 0 ? (
        <div className="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
          </svg>
          <h2>No links created</h2>
          <p>Create links between modules to establish connections.</p>
          <button className="button button-primary" onClick={handleCreateLink}>
            Create Link
          </button>
        </div>
      ) : (
        <div className="grid grid-1">
          {Array.from(createJobs.entries()).map(([linkId, job]) => (
            <div key={`create-${linkId}`} className="card">
              <InstallationProgress 
                moduleName={linkId} 
                job={job}
                operationType="create_link"
                onCancel={job.status === 'queued' ? () => {
                  if (job.id) {
                    const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
                    jobsApi.cancelJob({ id: job.id }).catch(err => 
                      console.error('Failed to cancel create link job:', err)
                    );
                  }
                } : undefined}
              />
            </div>
          ))}
          {links.map((link) => {
            const linkId = link.id || 'unknown';
            const moduleIds = Object.keys(link.modules || {});
            const createdDate = link.createdAt 
              ? new Date(link.createdAt).toLocaleDateString()
              : 'N/A';
            const deleteJob = deleteJobs.get(linkId);
            const isDeleting = !!deleteJob;

            return (
              <div key={linkId} className="card">
                <div className="link-header">
                  <div>
                    <h3 className="link-title">{linkId}</h3>
                    <p className="link-created">Created: {createdDate}</p>
                  </div>
                </div>

                {deleteJob && (
                  <InstallationProgress 
                    moduleName={linkId} 
                    job={deleteJob}
                    operationType="delete_link"
                    onCancel={deleteJob.status === 'queued' ? () => {
                      if (deleteJob.id) {
                        const jobsApi = new JobsApi(new Configuration({ basePath: '/api' }));
                        jobsApi.cancelJob({ id: deleteJob.id }).catch(err => 
                          console.error('Failed to cancel delete link job:', err)
                        );
                      }
                    } : undefined}
                  />
                )}

                {!isDeleting && (
                  <>
                    <div className="link-modules-section">
                      <h4 className="section-label">Modules</h4>
                      <div className="modules-list">
                        {moduleIds.map((moduleId, idx) => (
                          <div key={moduleId} className="module-item">
                            <span className="module-name">{moduleId}</span>
                            {link.references?.[moduleId] && (
                              <div className="module-references">
                                {Object.entries(link.references[moduleId]).map(([refKey, refValue]) => (
                                  <div key={refKey} className="reference-item">
                                    <span className="ref-label">{refKey}:</span>
                                    <code className="ref-value">{refValue as string}</code>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    {link.dependencyOrder && link.dependencyOrder.length > 0 && (
                      <div className="link-dependencies-section">
                        <h4 className="section-label">Dependency Order</h4>
                        <div className="dependency-flow">
                          {link.dependencyOrder.map((dep, idx) => (
                            <React.Fragment key={dep}>
                              <span className="dependency-item">{dep}</span>
                              {idx < link.dependencyOrder!.length - 1 && (
                                <span className="dependency-arrow">→</span>
                              )}
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                    )}

                    {link.sharedNetworks && link.sharedNetworks.length > 0 && (
                      <div className="link-networks-section">
                        <h4 className="section-label">Networks</h4>
                        <div className="networks-list">
                          {link.sharedNetworks.map((network) => (
                            <span key={network} className="tag">{network}</span>
                          ))}
                        </div>
                      </div>
                    )}

                    {link.tags && link.tags.length > 0 && (
                      <div className="link-tags">
                        {link.tags.map((tag) => (
                          <span key={tag} className="tag">{tag}</span>
                        ))}
                      </div>
                    )}

                    <div className="link-actions">
                      <button
                        className="button button-danger"
                        onClick={() => handleDeleteLink(linkId)}
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
