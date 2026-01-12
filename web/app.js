const API_BASE = '/api';

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tabName = btn.dataset.tab;
        switchTab(tabName);
    });
});

function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });

    // Deactivate all buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.tab === tabName) {
            btn.classList.add('active');
        }
    });

    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');

    // Load data for the tab
    if (tabName === 'modules') {
        loadModules();
    } else if (tabName === 'links') {
        loadLinks();
    } else if (tabName === 'exposures') {
        loadExposures();
    }
}

// Health check
async function checkHealth() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();
        updateHealthStatus(data.status === 'ok');
    } catch (error) {
        updateHealthStatus(false);
    }
}

function updateHealthStatus(isHealthy) {
    const indicator = document.getElementById('health-status');
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');

    if (isHealthy) {
        dot.classList.add('healthy');
        dot.classList.remove('error');
        text.textContent = 'Connected';
    } else {
        dot.classList.add('error');
        dot.classList.remove('healthy');
        text.textContent = 'Disconnected';
    }
}

// Load modules
async function loadModules() {
    try {
        const response = await fetch(`${API_BASE}/modules`);
        const data = await response.json();
        renderModules(data.modules || []);
    } catch (error) {
        console.error('Failed to load modules:', error);
        document.getElementById('modules-list').innerHTML = '<p class="error">Failed to load modules</p>';
    }
}

function renderModules(modules) {
    const container = document.getElementById('modules-list');

    if (modules.length === 0) {
        container.innerHTML = '<p class="loading">No modules installed</p>';
        return;
    }

    container.innerHTML = modules.map(module => `
        <div class="module-card">
            <div class="module-header">
                <div class="module-name">${module.id}</div>
                <div class="module-state ${module.state.toLowerCase()}">${module.state}</div>
            </div>
            <div class="module-info">
                <div>📦 Path: ${module.module_path}</div>
                ${module.ip_address ? `<div>🌐 IP: ${module.ip_address}</div>` : ''}
            </div>
            ${module.tags && module.tags.length > 0 ? `
                <div class="module-tags">
                    ${module.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                </div>
            ` : ''}
            <div class="module-actions">
                <button class="btn btn-secondary" onclick="showModuleDetails('${module.id}')">Details</button>
                <button class="btn btn-danger" onclick="uninstallModule('${module.id}')">Uninstall</button>
            </div>
        </div>
    `).join('');
}

// Load links
async function loadLinks() {
    try {
        const response = await fetch(`${API_BASE}/links`);
        const data = await response.json();
        renderLinks(data.links || []);
    } catch (error) {
        console.error('Failed to load links:', error);
        document.getElementById('links-list').innerHTML = '<p class="error">Failed to load links</p>';
    }
}

function renderLinks(links) {
    const container = document.getElementById('links-list');

    if (links.length === 0) {
        container.innerHTML = '<p class="loading">No links created</p>';
        return;
    }

    container.innerHTML = links.map(link => {
        const modulesList = Object.keys(link.apps || link.modules || {}).join(', ');
        return `
            <div class="list-item">
                <div class="list-item-info">
                    <div class="list-item-id">${link.id}</div>
                    <div class="list-item-details">Modules: ${modulesList}</div>
                    ${link.tags && link.tags.length > 0 ? `
                        <div style="margin-top: 8px;">
                            ${link.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                        </div>
                    ` : ''}
                </div>
                <div class="list-item-actions">
                    <button class="btn btn-danger" onclick="deleteLink('${link.id}')">Delete</button>
                </div>
            </div>
        `;
    }).join('');
}

// Load exposures
async function loadExposures() {
    try {
        const response = await fetch(`${API_BASE}/exposures`);
        const data = await response.json();
        renderExposures(data.exposures || []);
    } catch (error) {
        console.error('Failed to load exposures:', error);
        document.getElementById('exposures-list').innerHTML = '<p class="error">Failed to load exposures</p>';
    }
}

function renderExposures(exposures) {
    const container = document.getElementById('exposures-list');

    if (exposures.length === 0) {
        container.innerHTML = '<p class="loading">No exposures created</p>';
        return;
    }

    container.innerHTML = exposures.map(exp => `
        <div class="list-item">
            <div class="list-item-info">
                <div class="list-item-id">${exp.id}</div>
                <div class="list-item-details">
                    ${exp.hostname} → ${exp.module_id}:${exp.container_port}
                </div>
                ${exp.tags && exp.tags.length > 0 ? `
                    <div style="margin-top: 8px;">
                        ${exp.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                    </div>
                ` : ''}
            </div>
            <div class="list-item-actions">
                <button class="btn btn-danger" onclick="deleteExposure('${exp.id}')">Delete</button>
            </div>
        </div>
    `).join('');
}

// Modal functions
function showInstallModal() {
    document.getElementById('install-modal').style.display = 'flex';
    document.getElementById('install-form').style.display = 'block';
    document.getElementById('install-progress').style.display = 'none';
}

function showLinkModal() {
    document.getElementById('link-modal').style.display = 'flex';
}

function showExposureModal() {
    document.getElementById('exposure-modal').style.display = 'flex';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
}

// Close modal on outside click
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.style.display = 'none';
        }
    });
});

// Install module form
document.getElementById('install-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const source = document.getElementById('module-source').value;
    const moduleId = document.getElementById('module-id').value;
    const tagsStr = document.getElementById('module-tags').value;
    const arch = document.getElementById('module-arch').value;
    const gpuVendor = document.getElementById('module-gpu').value;

    const tags = tagsStr.split(',').map(t => t.trim()).filter(t => t);

    const payload = {
        source,
        module_id: moduleId,
        tags: tags.length > 0 ? tags : undefined,
        ...(arch && { arch }),
        ...(gpuVendor && { gpu_vendor: gpuVendor })
    };

    try {
        document.getElementById('install-form').style.display = 'none';
        document.getElementById('install-progress').style.display = 'block';

        const response = await fetch(`${API_BASE}/modules/${moduleId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const reader = response.body.getReader();
        const output = document.getElementById('install-output');
        output.innerHTML = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = new TextDecoder().decode(value);
            const lines = text.trim().split('\n');

            for (const line of lines) {
                if (line) {
                    try {
                        const json = JSON.parse(line);
                        output.innerHTML += `${json.status}: ${json.message}\n`;
                    } catch (e) {
                        output.innerHTML += line + '\n';
                    }
                }
            }
            output.parentElement.scrollTop = output.parentElement.scrollHeight;
        }

        output.innerHTML += '\n✅ Module installed successfully!';
        setTimeout(() => {
            closeModal('install-modal');
            loadModules();
        }, 2000);
    } catch (error) {
        document.getElementById('install-output').innerHTML = `❌ Error: ${error.message}`;
    }
});

// Create link form
document.getElementById('link-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const id = document.getElementById('link-id').value;
    const modulesJson = document.getElementById('link-modules').value;
    const tagsStr = document.getElementById('link-tags').value;

    const tags = tagsStr.split(',').map(t => t.trim()).filter(t => t);

    try {
        const modules = JSON.parse(modulesJson);
        const payload = { modules };
        if (tags.length > 0) payload.tags = tags;

        const response = await fetch(`${API_BASE}/links/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            closeModal('link-modal');
            loadLinks();
        } else {
            alert('Failed to create link');
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
});

// Create exposure form
document.getElementById('exposure-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const id = document.getElementById('exposure-id').value;
    const moduleId = document.getElementById('exposure-module').value;
    const hostname = document.getElementById('exposure-hostname').value;
    const protocol = document.getElementById('exposure-protocol').value;
    const port = parseInt(document.getElementById('exposure-port').value);
    const tagsStr = document.getElementById('exposure-tags').value;

    const tags = tagsStr.split(',').map(t => t.trim()).filter(t => t);

    const payload = {
        module_id: moduleId,
        hostname,
        protocol,
        container_port: port
    };
    if (tags.length > 0) payload.tags = tags;

    try {
        const response = await fetch(`${API_BASE}/exposures/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            closeModal('exposure-modal');
            loadExposures();
        } else {
            alert('Failed to create exposure');
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
});

// Delete functions
async function uninstallModule(moduleId) {
    if (!confirm(`Uninstall ${moduleId}?`)) return;

    try {
        const response = await fetch(`${API_BASE}/modules/${moduleId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadModules();
        } else {
            alert('Failed to uninstall module');
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function deleteLink(linkId) {
    if (!confirm(`Delete link ${linkId}?`)) return;

    try {
        const response = await fetch(`${API_BASE}/links/${linkId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadLinks();
        } else {
            alert('Failed to delete link');
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

async function deleteExposure(exposureId) {
    if (!confirm(`Delete exposure ${exposureId}?`)) return;

    try {
        const response = await fetch(`${API_BASE}/exposures/${exposureId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadExposures();
        } else {
            alert('Failed to delete exposure');
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

function showModuleDetails(moduleId) {
    alert(`Details for ${moduleId}\n\nFull details view coming soon!`);
}

// Initialize
window.addEventListener('load', () => {
    checkHealth();
    loadModules();

    // Check health periodically
    setInterval(checkHealth, 5000);
});
