import type { DagNode } from './api';

interface Props {
    nodes: DagNode[];
    depths: Map<string, number>;
    selectedId: string | null;
    onSelect: (id: string) => void;
    onLoadDemo?: () => void;
}

/** Get a short display value from a node's config or output. */
function nodeValue(node: DagNode): string {
    const c = node.config || {};
    const o = node.output || {};

    // Show the most useful field per type
    if (c.stable_id) return truncate(c.stable_id, 24);
    if (c.mountpoint) return c.mountpoint;
    if (c.path) return c.path;
    if (c.interface) return c.interface;
    if (c.driver) return `${c.driver}${c.version ? ' ' + c.version : ''}`;
    if (c.module_id) return c.module_id;
    if (c.filesystem) return c.filesystem;
    if (c.name) return `${c.name}=${c.value || ''}`;
    if (o.device_path) return truncate(o.device_path, 20);
    if (c.number) return `partition ${c.number}`;
    if (c.port) return `:${c.port}`;

    return node.status.replace('_', ' ');
}

function truncate(s: string, n: number): string {
    return s.length > n ? s.slice(0, n) + '…' : s;
}

export function NodeList({ nodes, depths, selectedId, onSelect, onLoadDemo }: Props) {
    if (nodes.length === 0) {
        return (
            <div class="node-list" style="color: var(--fg-dim); padding-top: 48px; text-align: center;">
                <div style="font-size: 18px; font-weight: 300; margin-bottom: 12px;">
                    no nodes
                </div>
                <div style="font-size: 13px; margin-bottom: 24px;">
                    build a graph via the API or load a demo
                </div>
                {onLoadDemo && (
                    <button class="btn primary" onClick={onLoadDemo}>
                        load demo graph
                    </button>
                )}
            </div>
        );
    }

    return (
        <div class="node-list">
            {nodes.map(node => {
                const depth = depths.get(node.id) || 0;
                const isSelected = node.id === selectedId;

                return (
                    <div
                        key={node.id}
                        class={`node-item ${isSelected ? 'selected' : ''}`}
                        onClick={() => onSelect(node.id)}
                    >
                        {depth > 0 && (
                            <div class="node-indent" style={`width: ${depth * 20}px`}>
                                {Array.from({ length: depth }).map((_, i) => (
                                    <div key={i} class="node-indent-line" />
                                ))}
                            </div>
                        )}
                        <div class={`status-dot ${node.status}`} />
                        <div class="node-name">{node.id}</div>
                        <div class="node-value">{nodeValue(node)}</div>
                    </div>
                );
            })}
        </div>
    );
}
