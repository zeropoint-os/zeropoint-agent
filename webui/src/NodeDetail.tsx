import type { DagNode, DagEdge } from './api';

interface Props {
    node: DagNode;
    allNodes: DagNode[];
    edges: DagEdge[];
    onNavigate: (id: string) => void;
}

export function NodeDetail({ node, allNodes, edges, onNavigate }: Props) {
    const nodeMap = new Map(allNodes.map(n => [n.id, n]));

    // Find parents and children from edges
    const parents = edges
        .filter(e => e.target === node.id)
        .map(e => nodeMap.get(e.source))
        .filter(Boolean) as DagNode[];

    const children = edges
        .filter(e => e.source === node.id)
        .map(e => nodeMap.get(e.target))
        .filter(Boolean) as DagNode[];

    const config = node.config || {};
    const output = node.output || {};

    return (
        <div class="detail">
            <div class="detail-header">
                <div class={`status-dot ${node.status}`} style="width: 14px; height: 14px;" />
                <div class="detail-name">{node.id}</div>
            </div>
            <div class="detail-type">
                {node.type} · {node.status.replace('_', ' ')}
            </div>

            {/* Error */}
            {node.error && (
                <div class="detail-section">
                    <div class="detail-section-title">error</div>
                    <div class="detail-error">{node.error}</div>
                </div>
            )}

            {/* Config */}
            {Object.keys(config).length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">config</div>
                    {Object.entries(config).map(([key, value]) => (
                        <div class="detail-field" key={key}>
                            <span class="detail-field-key">{key}</span>
                            <span class={`detail-field-value ${isVarRef(value) ? 'var-ref' : ''}`}>
                                {formatValue(value)}
                            </span>
                        </div>
                    ))}
                </div>
            )}

            {/* Output */}
            {Object.keys(output).length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">output</div>
                    {Object.entries(output).map(([key, value]) => (
                        <div class="detail-field" key={key}>
                            <span class="detail-field-key">{key}</span>
                            <span class="detail-field-value">{formatValue(value)}</span>
                        </div>
                    ))}
                </div>
            )}

            {/* Parents */}
            {parents.length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">parents</div>
                    {parents.map(p => (
                        <div class="detail-parent" key={p.id} onClick={() => onNavigate(p.id)}>
                            <div class={`status-dot ${p.status}`} />
                            <span class="node-name">{p.id}</span>
                            <span class="node-value">{p.status.replace('_', ' ')}</span>
                        </div>
                    ))}
                </div>
            )}

            {/* Children */}
            {children.length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">children</div>
                    {children.map(c => (
                        <div class="detail-child" key={c.id} onClick={() => onNavigate(c.id)}>
                            <div class={`status-dot ${c.status}`} />
                            <span class="node-name">{c.id}</span>
                            <span class="node-value">{c.status.replace('_', ' ')}</span>
                        </div>
                    ))}
                </div>
            )}

            {/* Actions */}
            <div class="actions">
                <button class="btn">edit</button>
                <button class="btn">retry</button>
                <button class="btn" style="color: var(--status-error); border-color: var(--status-error);">
                    remove
                </button>
            </div>
        </div>
    );
}

function isVarRef(value: any): boolean {
    return typeof value === 'string' && value.startsWith('${') && value.endsWith('}');
}

function formatValue(value: any): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}
