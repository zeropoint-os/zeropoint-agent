import React, { useState, useEffect } from 'react';
import { BootApi, Configuration, BootMarkerEntry, BootServiceMarkers } from 'artifacts/clients/typescript';
import './BootStatus.css';

interface LogEntry {
  timestamp: string;
  service: string;
  message: string;
  step?: string;
  isMarker?: boolean;
  level?: string;
}

interface ServiceStatus {
  service: string;
  state: 'pending' | 'running' | 'completed' | 'failed';
  currentStep: string;
  timestamp: string;
}

export default function BootStatus() {
  const [markersList, setMarkersList] = useState<BootServiceMarkers[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const bootApi = new BootApi(new Configuration({ basePath: '' }));

  useEffect(() => {
    let polling: number | null = null;

    const checkAndPoll = async () => {
      try {
        const data = await bootApi.apiBootStatusGet();
        // data is now BootServiceMarkers[] - array of {service, markers}
        setMarkersList(data || []);
        setError(null);
        // detect final marker
        let finalSeen = false;
        if (data && Array.isArray(data)) {
          for (const serviceMarkers of data) {
            for (const m of serviceMarkers.markers || []) {
              if (m.step === 'boot-complete') {
                finalSeen = true;
                break;
              }
            }
            if (finalSeen) break;
          }
        }

        if (finalSeen) {
          // stop polling
          if (polling !== null) {
            window.clearInterval(polling);
            polling = null;
          }
          setLoading(false);
        }
      } catch (err) {
        console.error('Failed to fetch status:', err);
        setError(`Failed to fetch status: ${err}`);
        setLoading(false);
      }
    };

    // initial check
    checkAndPoll();
    // poll every second until final marker seen
    polling = window.setInterval(checkAndPoll, 1000);

    return () => {
      if (polling !== null) window.clearInterval(polling);
    };
  }, []);

  // fetchStatus/polling handled in effect above; helper to manually refresh if needed
  const refreshStatusOnce = async () => {
    try {
      const data = await bootApi.apiBootStatusGet();
      setMarkersList(data);
      setError(null);
    } catch (err) {
      console.error('Failed to refresh status:', err);
      setError(`Failed to refresh status: ${err}`);
    }
  };

  // Build service list from markersList (array format)
  const getServiceStatuses = (): ServiceStatus[] => {
    if (!markersList) return [];
    
    // Find the service with the most-recent marker timestamp. Only that
    // service should be shown as 'running' (spinner). Older services with
    // markers become 'completed' (or keep other states if applicable).
    let latestSvc: string | null = null;
    let latestTs = 0;
    for (const serviceMarkers of markersList) {
      const list = serviceMarkers.markers || [];
      if (list.length === 0) continue;
      const last = list[list.length - 1];
      const ts = Date.parse(last.timestamp || '') || 0;
      if (ts > latestTs) {
        latestTs = ts;
        latestSvc = serviceMarkers.service || null;
      }
    }

    return markersList.map((serviceMarkers) => {
      const list = serviceMarkers.markers || [];
      const last = list.length > 0 ? list[list.length - 1] : undefined;
      let state: ServiceStatus['state'] = 'pending';
      if (!last) {
        state = 'pending';
      } else if (last.step === 'boot-complete') {
        state = 'completed';
      } else if ((serviceMarkers.service || '') === latestSvc) {
        state = 'running';
      } else {
        state = 'completed';
      }

      return {
        service: serviceMarkers.service || '',
        state,
        currentStep: last?.step || '',
        timestamp: last?.timestamp || new Date().toISOString(),
      } as ServiceStatus;
    });
  };

  const getBootStatus = (): string => {
    if (loading) return 'Initializing...';
    // determine if overall final marker seen
    if (markersList) {
      for (const serviceMarkers of markersList) {
        for (const m of serviceMarkers.markers || []) {
          if (m.step === 'boot-complete') return '✓ Boot Complete';
        }
      }
    }
    return 'In Progress';
  };

  if (loading && !markersList) {
    return <div className="boot-status-container">Loading boot status...</div>;
  }

  const serviceStatuses = getServiceStatuses();

  return (
    <div className="boot-status-container">
      <div className="boot-status-header">
        <h1>Boot Status</h1>
        <div className="boot-status-summary">
          <p>
            <strong>Status:</strong> {getBootStatus()}
          </p>
          {/* show completed timestamp if we can find the final marker */}
          {markersList && (() => {
            for (const serviceMarkers of markersList) {
              for (const m of serviceMarkers.markers || []) {
                if (m.step === 'boot-complete') {
                  return <p><strong>Completed:</strong> {new Date(m.timestamp || '').toLocaleString()}</p>;
                }
              }
            }
            return null;
          })()}
        </div>
      </div>

      {error && <div className="boot-status-error">{error}</div>}

      <div className="boot-services">
        <h2>Services</h2>
        {serviceStatuses.length === 0 ? (
          <p className="no-services">Waiting for boot logs...</p>
        ) : (
          <div className="services-list">
            {serviceStatuses.map((svc) => (
              <div key={svc.service} className={`service-row ${svc.state}`}>
                <div className="service-indicator">
                  {svc.state === 'failed' && '✗'}
                  {svc.state === 'completed' && '✓'}
                  {svc.state === 'running' && <span className="spinner"></span>}
                  {svc.state === 'pending' && ''}
                </div>
                <div className="service-info">
                  <div className="service-name">{svc.service.replace('zeropoint-', '')}</div>
                  {svc.currentStep && <div className="service-step">{svc.currentStep}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

