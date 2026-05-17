import type { DagNode } from './api';

interface TileProps {
    node: DagNode;
    onClick: () => void;
    childCount?: number;
    // If provided, the tile's name is shown relative to this path (the leaf
    // after stripping the prefix). Otherwise the full id is shown.
    parentPath?: string;
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

function displayName(id: string, parentPath?: string): string {
    if (parentPath && id.startsWith(parentPath + '/')) {
        return id.slice(parentPath.length + 1);
    }
    return id;
}

export function Tile({ node, onClick, childCount, parentPath }: TileProps) {
    return (
        <div class="tile" onClick={onClick}>
            <div>
                <div class="tile-header">
                    <div class={`status-dot ${node.status}`} />
                    <div class="tile-name">{displayName(node.id, parentPath)}</div>
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
