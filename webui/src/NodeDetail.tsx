import type { DagNode, DagEdge } from './api';
import { Tile } from './Tile';

interface Props {
    node: DagNode;
    allNodes: DagNode[];
    edges: DagEdge[];
    onNavigate: (id: string) => void;
}

export function NodeDetail({ node, allNodes, edges, onNavigate }: Props) {
    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const childrenOf = (id: string) => edges.filter(e => e.source === id).map(e => nodeMap.get(e.target)).filter(Boolean) as DagNode[];
    const children = childrenOf(node.id);

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

            {node.error && (
                <div class="detail-section">
                    <div class="detail-error">{node.error}</div>
                </div>
            )}

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

            {children.length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">children</div>
                    <div class="tiles" style="padding: 0;">
                        {children.map(c => (
                            <Tile
                                key={c.id}
                                node={c}
                                onClick={() => onNavigate(c.id)}
                                childCount={childrenOf(c.id).length}
                            />
                        ))}
                    </div>
                </div>
            )}

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
