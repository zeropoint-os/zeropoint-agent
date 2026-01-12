import React, { useState, useEffect } from 'react';
import './CreateExposureDialog.css';

interface Module {
  id?: string;
  module_path?: string;
  state?: string;
  container_id?: string;
  container_name?: string;
  ip_address?: string;
  containers?: any;
  tags?: string[];
  [key: string]: any;
}

interface PortOption {
  name: string;
  port: number;
  protocol: string;
}

interface CreateExposureDialogProps {
  isOpen: boolean;
  modules: Module[];
  onClose: () => void;
  onCreate: (data: {
    module_id: string;
    hostname: string;
    protocol: string;
    container_port: number;
  }) => Promise<void>;
}

export default function CreateExposureDialog({ isOpen, modules, onClose, onCreate }: CreateExposureDialogProps) {
  const [selectedModule, setSelectedModule] = useState<string>('');
  const [hostname, setHostname] = useState('');
  const [selectedPort, setSelectedPort] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getModulePorts = (module: Module): PortOption[] => {
    const ports: PortOption[] = [];
    if (module.containers && typeof module.containers === 'object') {
      Object.values(module.containers).forEach((container: any) => {
        if (container.ports && typeof container.ports === 'object') {
          Object.entries(container.ports).forEach(([portName, portInfo]: [string, any]) => {
            if (portInfo.port && portInfo.protocol) {
              ports.push({
                name: portName,
                port: portInfo.port,
                protocol: portInfo.protocol
              });
            }
          });
        }
      });
    }
    return ports;
  };

  const currentModule = modules.find(m => m.id === selectedModule);
  const availablePorts = currentModule ? getModulePorts(currentModule) : [];
  
  const selectedPortInfo = availablePorts.find(
    (p) => `${p.protocol}-${p.port}` === selectedPort
  );

  const previewUrl = selectedPortInfo && hostname 
    ? `${selectedPortInfo.protocol}://${hostname}.local:${selectedPortInfo.port}`
    : '';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedModule || !hostname || !selectedPortInfo) {
      setError('Please fill in all fields');
      return;
    }

    try {
      setLoading(true);
      setError(null);

      await onCreate({
        module_id: selectedModule,
        hostname,
        protocol: selectedPortInfo.protocol,
        container_port: selectedPortInfo.port
      });

      // Reset form
      setSelectedModule('');
      setHostname('');
      setSelectedPort('');
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create exposure');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      <div className="modal-overlay" onClick={onClose} />
      <div className="modal">
        <div className="modal-header">
          <h2>Create Exposure</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          {error && (
            <div className="form-error">
              <p>{error}</p>
            </div>
          )}

          <div className="form-group">
            <label htmlFor="module-select">Module</label>
            <select
              id="module-select"
              value={selectedModule}
              onChange={(e) => {
                setSelectedModule(e.target.value);
                setSelectedPort(''); // Reset port when module changes
              }}
              disabled={loading}
              required
            >
              <option value="">Select a module...</option>
              {modules.map((module) => (
                <option key={module.id} value={module.id}>
                  {module.id}
                </option>
              ))}
            </select>
          </div>

          {selectedModule && (
            <>
              <div className="form-group">
                <label htmlFor="port-select">Port & Protocol</label>
                <select
                  id="port-select"
                  value={selectedPort}
                  onChange={(e) => setSelectedPort(e.target.value)}
                  disabled={loading || availablePorts.length === 0}
                  required
                >
                  <option value="">Select a port...</option>
                  {availablePorts.map((port) => (
                    <option key={`${port.protocol}-${port.port}`} value={`${port.protocol}-${port.port}`}>
                      {port.protocol.toUpperCase()} {port.port} ({port.name})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="hostname-input">Hostname</label>
                <input
                  id="hostname-input"
                  type="text"
                  value={hostname}
                  onChange={(e) => setHostname(e.target.value)}
                  placeholder="e.g., ollama"
                  disabled={loading}
                  required
                />
                {previewUrl && (
                  <div className="url-preview">
                    <span className="preview-label">Preview:</span>
                    <code>{previewUrl}</code>
                  </div>
                )}
              </div>
            </>
          )}

          <div className="modal-footer">
            <button
              type="button"
              className="button button-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="button button-primary"
              disabled={loading || !selectedModule || !hostname || !selectedPortInfo}
            >
              {loading ? 'Creating...' : 'Create Exposure'}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
