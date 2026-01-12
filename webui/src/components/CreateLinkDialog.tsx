import React, { useState, useEffect } from 'react';
import './CreateLinkDialog.css';

interface Module {
  id?: string;
  [key: string]: any;
}

interface InputSchema {
  type: string;
  description?: string;
  default_value?: any;
  current_value?: any;
  required: boolean;
  system_managed: boolean;
}

interface OutputSchema {
  description?: string;
  current_value?: any;
}

interface InspectResponse {
  module_id: string;
  inputs: { [key: string]: InputSchema };
  outputs: { [key: string]: OutputSchema };
}

interface CreateLinkDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (data: {
    id: string;
    modules: { [moduleId: string]: { [key: string]: string } };
  }) => Promise<void>;
}

export default function CreateLinkDialog({ isOpen, onClose, onCreate }: CreateLinkDialogProps) {
  const [allModules, setAllModules] = useState<Module[]>([]);
  const [selectedModules, setSelectedModules] = useState<string[]>([]);
  const [inspectData, setInspectData] = useState<{ [moduleId: string]: InspectResponse }>({});
  const [inputValues, setInputValues] = useState<{ [moduleId: string]: { [key: string]: string } }>({});
  const [moduleToAdd, setModuleToAdd] = useState<string>('');
  const [linkName, setLinkName] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      fetchModules();
    }
  }, [isOpen]);

  const fetchModules = async () => {
    try {
      const response = await fetch('/api/modules');
      if (!response.ok) {
        throw new Error('Failed to fetch modules');
      }
      const data = await response.json();
      const moduleList = Array.isArray(data) ? data : (data.modules || []);
      setAllModules(moduleList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch modules');
    }
  };

  const fetchInspectData = async (moduleId: string) => {
    try {
      const response = await fetch(`/api/modules/${moduleId}/inspect`);
      if (!response.ok) {
        throw new Error(`Failed to inspect module ${moduleId}`);
      }
      const data: InspectResponse = await response.json();
      return data;
    } catch (err) {
      throw new Error(err instanceof Error ? err.message : `Failed to inspect ${moduleId}`);
    }
  };

  const handleAddModule = async () => {
    if (!moduleToAdd) {
      setError('Please select a module to add');
      return;
    }

    if (selectedModules.includes(moduleToAdd)) {
      setError('This module is already selected');
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const inspect = await fetchInspectData(moduleToAdd);
      
      setSelectedModules([...selectedModules, moduleToAdd]);
      setInspectData({
        ...inspectData,
        [moduleToAdd]: inspect
      });

      // Initialize input values for this module
      const moduleInputs: { [key: string]: string } = {};
      Object.entries(inspect.inputs).forEach(([inputName, inputSchema]) => {
        if (!inputSchema.system_managed) {
          moduleInputs[inputName] = '';
        }
      });

      setInputValues({
        ...inputValues,
        [moduleToAdd]: moduleInputs
      });

      // Reset the selector
      setModuleToAdd('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add module');
    } finally {
      setLoading(false);
    }
  };

  const handleRemoveModule = (moduleId: string) => {
    setSelectedModules(selectedModules.filter(m => m !== moduleId));
    const newInspectData = { ...inspectData };
    delete newInspectData[moduleId];
    setInspectData(newInspectData);

    const newInputValues = { ...inputValues };
    delete newInputValues[moduleId];
    setInputValues(newInputValues);
  };

  const handleInputChange = (moduleId: string, inputName: string, value: string) => {
    setInputValues({
      ...inputValues,
      [moduleId]: {
        ...inputValues[moduleId],
        [inputName]: value
      }
    });
  };

  const getAvailableOutputs = (exceptModule: string): { label: string; value: string }[] => {
    const outputs: { label: string; value: string }[] = [];

    selectedModules.forEach(moduleId => {
      if (moduleId === exceptModule) return;

      const inspect = inspectData[moduleId];
      if (!inspect || !inspect.outputs) return;

      Object.keys(inspect.outputs).forEach(outputName => {
        outputs.push({
          label: `${moduleId}.${outputName}`,
          value: `${moduleId}.${outputName}`
        });
      });
    });

    return outputs;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!linkName.trim()) {
      setError('Please provide a link name');
      return;
    }

    if (selectedModules.length === 0) {
      setError('Please select at least one module');
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Build the modules object with wrapped values
      const modulesData: { [moduleId: string]: { [key: string]: string } } = {};

      selectedModules.forEach(moduleId => {
        const inputs = inspectData[moduleId]?.inputs || {};
        const values = inputValues[moduleId] || {};

        modulesData[moduleId] = {};

        Object.entries(values).forEach(([inputName, value]) => {
          if (value && !inputs[inputName]?.system_managed) {
            // Wrap value in ${}
            modulesData[moduleId][inputName] = `\${${value}}`;
          }
        });
      });

      // POST to /api/links/{linkName}
      const response = await fetch(`/api/links/${linkName}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          modules: modulesData
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.message || `Failed to create link: ${response.statusText}`);
      }

      // Call the onCreate callback
      await onCreate({
        id: linkName,
        modules: modulesData
      });

      // Reset form
      setSelectedModules([]);
      setInspectData({});
      setInputValues({});
      setLinkName('');
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create link');
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
          <h2>Create Link</h2>
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

          {/* Link Name Field */}
          <div className="form-group">
            <label htmlFor="link-name" className="form-label">Link Name *</label>
            <input
              type="text"
              id="link-name"
              value={linkName}
              onChange={(e) => setLinkName(e.target.value)}
              placeholder="e.g., ollama-openwebui"
              disabled={loading}
              required
              className="form-input"
            />
            <p className="form-hint">A unique identifier for this link (e.g., ollama-openwebui)</p>
          </div>

          {/* Add Module Section */}
          <div className="add-module-section">
            <select
              value={moduleToAdd}
              onChange={(e) => setModuleToAdd(e.target.value)}
              disabled={loading}
              className="module-select"
            >
              <option value="">Select module to add...</option>
              {allModules
                .filter(m => !selectedModules.includes(m.id as string))
                .map(m => (
                  <option key={m.id} value={m.id as string}>
                    {m.id}
                  </option>
                ))}
            </select>
            <button
              type="button"
              className="button button-secondary"
              onClick={handleAddModule}
              disabled={loading || !moduleToAdd}
            >
              <span>+</span> Add
            </button>
          </div>

          {/* Selected Modules List */}
          {selectedModules.length > 0 && (
            <div className="modules-container">
              {selectedModules.map((moduleId) => {
                const inspect = inspectData[moduleId];
                const values = inputValues[moduleId] || {};
                const otherOutputs = getAvailableOutputs(moduleId);

                // Filter inputs to exclude system-managed ones
                const userInputs = inspect
                  ? Object.entries(inspect.inputs).filter(
                      ([_, inputSchema]) => !inputSchema.system_managed
                    )
                  : [];

                return (
                  <div key={moduleId} className="module-card">
                    <div className="module-card-header">
                      <h4 className="module-name">{moduleId}</h4>
                      <button
                        type="button"
                        className="button button-icon button-danger"
                        onClick={() => handleRemoveModule(moduleId)}
                        title="Remove module"
                      >
                        −
                      </button>
                    </div>

                    {userInputs.length > 0 ? (
                      <div className="module-inputs">
                        {userInputs.map(([inputName, inputSchema]) => (
                          <div key={inputName} className="input-field">
                            <div className="input-row">
                              <span className="input-label">VARIABLE:</span>
                              <span className="variable-name">{inputName}</span>
                            </div>
                            <div className="input-row">
                              <span className="input-label">VALUE:</span>
                              <div className="input-combo">
                                <input
                                  type="text"
                                  value={values[inputName] || ''}
                                  onChange={(e) =>
                                    handleInputChange(moduleId, inputName, e.target.value)
                                  }
                                  placeholder={inputSchema.description || 'Enter value...'}
                                  disabled={loading}
                                  className="combo-input"
                                />
                                {otherOutputs.length > 0 && (
                                  <select
                                    onChange={(e) => {
                                      if (e.target.value) {
                                        handleInputChange(moduleId, inputName, e.target.value);
                                        e.target.value = '';
                                      }
                                    }}
                                    disabled={loading}
                                    className="combo-select"
                                    title="Select from available outputs"
                                  >
                                    <option value="">+ Suggest...</option>
                                    {otherOutputs.map((output) => (
                                      <option key={output.value} value={output.value}>
                                        {output.label}
                                      </option>
                                    ))}
                                  </select>
                                )}
                              </div>
                            </div>
                            {inputSchema.description && (
                              <p className="input-description">{inputSchema.description}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="no-inputs">No user-configurable inputs for this module</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {selectedModules.length === 0 && !error && (
            <div className="empty-message">
              <p>Add modules to create a link between them</p>
            </div>
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
              disabled={loading || !linkName.trim() || selectedModules.length === 0}
            >
              {loading ? 'Creating...' : 'Create Link'}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
