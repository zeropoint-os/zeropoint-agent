import type { DagNode, DagEdge } from './api';
import { Tile } from './Tile';

interface Props {
    node: DagNode;
    allNodes: DagNode[];
    edges: DagEdge[];
    onNavigate: (id: string) => void;
}

// Fields rendered in the detail header — don't repeat them in the property list.
const HEADER_FIELDS = new Set(['id', 'type', 'status']);

// Fields that have their own dedicated section.
const SECTION_FIELDS = new Set(['error']);

export function NodeDetail({ node, allNodes, edges, onNavigate }: Props) {
    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const childrenOf = (id: string) => edges.filter(e => e.source === id).map(e => nodeMap.get(e.target)).filter(Boolean) as DagNode[];
    const children = childrenOf(node.id);

    // Leaf name + parent path for the header.
    const lastSlash = node.id.lastIndexOf('/');
    const leaf = lastSlash >= 0 ? node.id.slice(lastSlash + 1) : node.id;
    const parentPath = lastSlash >= 0 ? node.id.slice(0, lastSlash) : '';

    // Flatten config dict into top-level properties, treat every other
    // top-level field of the node response as a property. Property
    // inspector — render whatever the server says the node carries.
    const props: Array<[string, any]> = [];
    for (const [key, value] of Object.entries(node)) {
        if (HEADER_FIELDS.has(key)) continue;
        if (SECTION_FIELDS.has(key)) continue;
        if (key === 'config' && value && typeof value === 'object') {
            for (const [ck, cv] of Object.entries(value)) {
                props.push([ck, cv]);
            }
            continue;
        }
        props.push([key, value]);
    }

    return (
        <div class="detail">
            <div class="detail-header">
                <div class={`status-dot ${node.status}`} style="width: 14px; height: 14px;" />
                <div class="detail-name">{leaf}</div>
            </div>
            <div class="detail-type">
                {parentPath && <span style="opacity: 0.6;">{parentPath} · </span>}
                {node.type} · {node.status.replace('_', ' ')}
            </div>

            {node.error && (
                <div class="detail-section">
                    <div class="detail-error">{node.error}</div>
                </div>
            )}

            {props.length > 0 && (
                <div class="detail-section">
                    <div class="detail-section-title">properties</div>
                    {props.map(([key, value]) => (
                        <div class="detail-field" key={key}>
                            <span class="detail-field-key">{key}</span>
                            <span class={`detail-field-value ${isVarRef(value) ? 'var-ref' : ''}`}>
                                <Value value={value} />
                            </span>
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
                                parentPath={node.id}
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

function Value({ value }: { value: any }) {
    if (value === null || value === undefined || value === '') return <span>—</span>;
    if (typeof value === 'boolean') return <span>{value ? 'true' : 'false'}</span>;
    if (typeof value === 'string' || typeof value === 'number')
        return <span>{String(value)}</span>;

    if (Array.isArray(value)) {
        if (value.length === 0) return <span>—</span>;
        return (
            <div style="padding-left: 0; margin-top: 4px;">
                {value.map((item, i) => (
                    <div key={i} style="padding: 4px 0; border-bottom: 1px solid var(--tile-bg);">
                        <Value value={item} />
                    </div>
                ))}
            </div>
        );
    }

    if (typeof value === 'object') {
        const entries = Object.entries(value).filter(([_, v]) => v !== null && v !== undefined);
        if (entries.length === 0) return <span>—</span>;
        return (
            <div style="margin-top: 4px;">
                {entries.map(([k, v]) => (
                    <div class="detail-field" key={k} style="padding: 2px 0;">
                        <span class="detail-field-key" style="font-size: 12px;">{k}</span>
                        <span class="detail-field-value" style="font-size: 12px;">
                            <Value value={v} />
                        </span>
                    </div>
                ))}
            </div>
        );
    }

    return <span>{String(value)}</span>;
}
