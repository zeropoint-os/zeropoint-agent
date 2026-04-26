import type { DagNode } from './api';

interface TileProps {
    node: DagNode;
    onClick: () => void;
    childCount?: number;
}

function tileValue(node: DagNode): string {
    const c = node.config || {};
    if (c.stable_id) return truncate(c.stable_id, 22);
    if (c.mountpoint) return c.mountpoint;
    if (c.path) return c.path;
    if (c.interface) return c.interface;
    if (c.driver) return `${c.driver} ${c.version || ''}`;
    if (c.module_id) return c.module_id;
    if (c.filesystem) return c.filesystem;
    if (c.name) return `${c.name}`;
    if (c.port) return `:${c.port}`;
    if (c.number) return `partition ${c.number}`;
    return '';
}

function truncate(s: string, n: number): string {
    return s.length > n ? s.slice(0, n) + '…' : s;
}

export function Tile({ node, onClick, childCount }: TileProps) {
    return (
        <div class="tile" onClick={onClick}>
            <div>
                <div class="tile-header">
                    <div class={`status-dot ${node.status}`} />
                    <div class="tile-name">{node.id}</div>
                </div>
                <div class="tile-value">
                    {tileValue(node)}
                    {childCount !== undefined && childCount > 0 && (
                        <span> · {childCount} {childCount === 1 ? 'child' : 'children'}</span>
                    )}
                </div>
            </div>
            <div class="tile-status">
                {node.status.replace('_', ' ')}
            </div>
        </div>
    );
}
