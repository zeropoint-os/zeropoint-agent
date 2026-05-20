import { useState } from 'preact/hooks';
import type { DagNode, DagEdge, NodeTypeSchema } from './api';
import { Tile } from './Tile';
import { PropertyInspector } from './PropertyInspector';
import { TypePicker } from './TypePicker';

interface Props {
    node: DagNode;
    allNodes: DagNode[];
    edges: DagEdge[];
    onNavigate: (id: string) => void;
    schema?: NodeTypeSchema;
    /** All schemas keyed by short picker name. */
    pickerSchemas: Record<string, NodeTypeSchema>;
}

export function NodeDetail({ node, allNodes, edges, onNavigate, schema, pickerSchemas }: Props) {
    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const childrenOf = (id: string) => edges.filter(e => e.source === id).map(e => nodeMap.get(e.target)).filter(Boolean) as DagNode[];
    const children = childrenOf(node.id);

    // Leaf name + parent path for the header.
    const lastSlash = node.id.lastIndexOf('/');
    const leaf = lastSlash >= 0 ? node.id.slice(lastSlash + 1) : node.id;
    const parentPath = lastSlash >= 0 ? node.id.slice(0, lastSlash) : '';

    // We show an "add" tile in the children grid only when this node is
    // a writable namespace: type is NamespaceNode AND effective_perms
    // grants 'w'. The check is intentionally permissive — the server
    // re-checks on POST and will 403 if the user is wrong.
    const effPerms = node.effective_perms || '';
    const canAddChildren = node.type === 'NamespaceNode' && effPerms.includes('w');

    const [pickerOpen, setPickerOpen] = useState(false);
    const onPickType = (typeName: string) => {
        setPickerOpen(false);
        // Encode parent id in the route — slashes are allowed in the
        // hash, so just inline. Type name is always a simple identifier.
        window.location.hash = `#/_new/${node.id}/${typeName}`;
    };

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

            <PropertyInspector node={node} schema={schema} />

            {(children.length > 0 || canAddChildren) && (
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
                        {canAddChildren && (
                            <button
                                class="tile tile-add"
                                onClick={() => setPickerOpen(true)}
                                aria-label="add child"
                            >
                                <div class="tile-add-icon">+</div>
                                <div class="tile-add-label">add</div>
                            </button>
                        )}
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

            {pickerOpen && (
                <TypePicker
                    parentId={node.id}
                    schemas={pickerSchemas}
                    onClose={() => setPickerOpen(false)}
                    onPick={onPickType}
                />
            )}
        </div>
    );
}
