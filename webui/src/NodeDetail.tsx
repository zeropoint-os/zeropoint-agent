import { useState, useEffect } from 'preact/hooks';
import type { DagNode, DagEdge, NodeTypeSchema } from './api';
import { updateNode, deleteNode, resolveNode, linkVar, unlinkVar, exposeService, unexposeService } from './api';
import { Tile } from './Tile';
import { PropertyInspector } from './PropertyInspector';
import { TypePicker } from './TypePicker';
import { VarPicker } from './VarPicker';

// Hardcoded tag display order for categorized children views.
// Untagged children fall through to the trailing "other" bucket.
// See zeropoint-agent/tags-model.
const TAG_DISPLAY_ORDER: Array<{ tag: string; label: string }> = [
    { tag: 'input',     label: 'inputs' },
    { tag: 'output',    label: 'outputs' },
    { tag: 'service',   label: 'services' },
    { tag: 'exposure',  label: 'exposures' },
    { tag: 'terraform', label: 'terraform' },
    { tag: 'system',    label: 'system' },
];

interface ChildGroup { tag: string; label: string; children: DagNode[]; }

function groupChildrenByTag(children: DagNode[]): ChildGroup[] {
    // Bucket each child under its highest-priority tag in the display
    // order; anything without a matching tag goes into "other". If all
    // children share zero tags (e.g. a brand-new namespace) we keep a
    // single anonymous group so the page renders one flat grid.
    const buckets = new Map<string, DagNode[]>();
    for (const c of children) {
        const tags = c.tags || [];
        let bucket = 'other';
        for (const { tag } of TAG_DISPLAY_ORDER) {
            if (tags.includes(tag)) { bucket = tag; break; }
        }
        const list = buckets.get(bucket) || [];
        list.push(c);
        buckets.set(bucket, list);
    }
    const groups: ChildGroup[] = [];
    for (const { tag, label } of TAG_DISPLAY_ORDER) {
        const kids = buckets.get(tag);
        if (kids && kids.length > 0) groups.push({ tag, label, children: kids });
    }
    const others = buckets.get('other');
    if (others && others.length > 0) {
        // Only show a label for "other" if there are other groups already.
        const label = groups.length > 0 ? 'other' : '';
        groups.push({ tag: 'other', label, children: others });
    }
    return groups;
}

interface Props {
    node: DagNode;
    allNodes: DagNode[];
    edges: DagEdge[];
    onNavigate: (id: string) => void;
    schema?: NodeTypeSchema;
    /** All schemas keyed by short picker name. */
    pickerSchemas: Record<string, NodeTypeSchema>;
    /** Called after a server-side change (save/delete/resolve) to refresh. */
    onChanged?: () => void;
}

export function NodeDetail({
    node, allNodes, edges, onNavigate, schema, pickerSchemas, onChanged,
}: Props) {
    const nodeMap = new Map(allNodes.map(n => [n.id, n]));
    const childrenOf = (id: string) => edges.filter(e => e.source === id).map(e => nodeMap.get(e.target)).filter(Boolean) as DagNode[];
    const children = childrenOf(node.id);

    const lastSlash = node.id.lastIndexOf('/');
    const leaf = lastSlash >= 0 ? node.id.slice(lastSlash + 1) : node.id;
    const parentPath = lastSlash >= 0 ? node.id.slice(0, lastSlash) : '';

    const effPerms = node.effective_perms || '';
    const canAddChildren = node.type === 'Namespace' && effPerms.includes('w');
    const canEdit = effPerms.includes('w');
    const canDelete = effPerms.includes('d');

    // --- Edit-mode state ---------------------------------------------
    // `draft` holds the in-flight values; seeded from node.config + node.perms
    // when entering edit mode and discarded on cancel.
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<Record<string, any>>({});
    const [busy, setBusy] = useState<null | 'save' | 'resolve' | 'delete'>(null);
    const [opError, setOpError] = useState<string | null>(null);

    // Reset edit state whenever we switch nodes.
    useEffect(() => {
        setEditing(false);
        setDraft({});
        setBusy(null);
        setOpError(null);
    }, [node.id]);

    const startEdit = () => {
        setEditing(true);
        setOpError(null);
        setDraft({ ...(node.config || {}), perms: node.perms ?? '***' });
    };
    const cancelEdit = () => {
        setEditing(false);
        setDraft({});
        setOpError(null);
    };
    const onFieldChange = (name: string, value: any) => {
        setDraft(d => ({ ...d, [name]: value }));
    };

    const onSave = async () => {
        setBusy('save'); setOpError(null);
        const { perms: newPerms, ...newConfig } = draft;
        // Only send fields that actually changed, to keep the wire
        // payload small and avoid no-op resets.
        const currentConfig = node.config || {};
        const changedConfig: Record<string, any> = {};
        for (const [k, v] of Object.entries(newConfig)) {
            if (JSON.stringify(currentConfig[k]) !== JSON.stringify(v)) {
                changedConfig[k] = v;
            }
        }
        const body: { config?: Record<string, any>; perms?: string } = {};
        if (Object.keys(changedConfig).length > 0) body.config = changedConfig;
        if (newPerms !== node.perms) body.perms = newPerms;

        if (Object.keys(body).length === 0) {
            // Nothing changed — just exit edit mode.
            setBusy(null);
            setEditing(false);
            return;
        }
        const res = await updateNode(node.id, body);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        setEditing(false);
        setDraft({});
        onChanged?.();
    };

    const onResolve = async () => {
        setBusy('resolve'); setOpError(null);
        const res = await resolveNode(node.id);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        onChanged?.();
    };

    const onRemove = async () => {
        if (!window.confirm(`Remove ${node.id}? This cannot be undone.`)) return;
        setBusy('delete'); setOpError(null);
        const res = await deleteNode(node.id);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        onChanged?.();
        // Navigate up to the parent if there is one.
        if (parentPath) onNavigate(parentPath);
    };

    // --- Add-child picker --------------------------------------------
    const [pickerOpen, setPickerOpen] = useState(false);
    const onPickType = (typeName: string) => {
        setPickerOpen(false);
        window.location.hash = `#/_new/${node.id}/${typeName}`;
    };

    // --- Var linking picker (only on Var-family nodes) ---------------
    const [varPickerOpen, setVarPickerOpen] = useState(false);

    // Linking is an atomic operation — the link itself IS the user's
    // commit. We exit edit mode after a successful link/unlink so the
    // user doesn't accidentally clobber the change by clicking 'save'
    // with the stale draft (the draft was seeded from node.config
    // before the link cleared it; saving would put the old literal
    // back, leaving the graph in a contradictory linked-but-literal
    // state).
    const onPickLinkTarget = async (targetId: string) => {
        setVarPickerOpen(false);
        setBusy('save'); setOpError(null);
        const res = await linkVar(node.id, targetId);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        setEditing(false);
        setDraft({});
        onChanged?.();
    };
    const onUnlinkSelf = async () => {
        setBusy('save'); setOpError(null);
        const res = await unlinkVar(node.id);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        setEditing(false);
        setDraft({});
        onChanged?.();
    };

    // --- Expose / Unexpose / Open (Service + Exposure) ---------------
    //
    // A Service node represents a discovered {port, protocol} bundle
    // from a module's terraform output. It's exposed if any Exposure
    // node is a child of it. Exposure shows an Open link for http
    // protocols.
    const isService = node.type === 'Service';
    const targetingExposures = allNodes.filter(n =>
        n.type === 'Exposure' && n.parents.includes(node.id));
    const isExposed = isService && targetingExposures.length > 0;
    const isExposure = node.type === 'Exposure';
    const httpUrl = (isExposure && (node.config?.protocol === 'http' || node.output?.protocol === 'http')
                     && node.config?.name)
        ? `http://${node.config.name}.local/`
        : null;

    const onExpose = async () => {
        setBusy('save'); setOpError(null);
        const res = await exposeService(node.id);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        onChanged?.();
    };
    const onUnexpose = async () => {
        setBusy('save'); setOpError(null);
        const res = await unexposeService(node.id);
        setBusy(null);
        if (res.error) { setOpError(res.error); return; }
        onChanged?.();
    };

    // Exclusion set for the var picker: this node + all its descendants
    // (server would reject those anyway; client filtering is for UX).
    const descendantsOf = (id: string): Set<string> => {
        const out = new Set<string>([id]);
        const queue = [id];
        while (queue.length > 0) {
            const cur = queue.shift()!;
            for (const c of childrenOf(cur)) {
                if (!out.has(c.id)) {
                    out.add(c.id);
                    queue.push(c.id);
                }
            }
        }
        return out;
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

            {opError && (
                <div class="detail-section">
                    <div class="detail-error">{opError}</div>
                </div>
            )}

            <PropertyInspector
                node={node}
                schema={schema}
                editable={editing}
                values={editing ? draft : undefined}
                onChange={editing ? onFieldChange : undefined}
                allNodes={allNodes}
                onLink={() => setVarPickerOpen(true)}
                onUnlink={onUnlinkSelf}
                onNavigateTo={onNavigate}
            />

            {(children.length > 0 || canAddChildren) && !editing && (
                <div class="detail-section">
                    <div class="detail-section-title">children</div>
                    {groupChildrenByTag(children).map(group => (
                        <div key={group.tag} class="children-group">
                            {group.label && (
                                <div class="children-group-label">{group.label}</div>
                            )}
                            <div class="tiles" style="padding: 0;">
                                {group.children.map(c => (
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
                    ))}
                    {canAddChildren && (
                        <div class="tiles" style="padding: 0;">
                            <button
                                class="tile tile-add"
                                onClick={() => setPickerOpen(true)}
                                aria-label="add child"
                            >
                                <div class="tile-add-icon">+</div>
                                <div class="tile-add-label">add</div>
                            </button>
                        </div>
                    )}
                </div>
            )}

            <div class="actions">
                {editing ? (
                    <>
                        <button
                            class="btn primary"
                            onClick={onSave}
                            disabled={busy !== null}
                        >{busy === 'save' ? 'saving…' : 'save'}</button>
                        <button class="btn" onClick={cancelEdit} disabled={busy !== null}>cancel</button>
                    </>
                ) : (
                    <>
                        <button
                            class="btn"
                            onClick={startEdit}
                            disabled={!canEdit || busy !== null}
                            title={canEdit ? '' : `not writable (effective perms: ${effPerms || '***'})`}
                        >edit</button>
                        <button
                            class="btn"
                            onClick={onResolve}
                            disabled={busy !== null}
                        >{busy === 'resolve' ? 'resolving…' : 'resolve'}</button>
                        {isService && !isExposed && (
                            <button
                                class="btn"
                                onClick={onExpose}
                                disabled={busy !== null}
                                title="expose this service via Envoy"
                            >expose</button>
                        )}
                        {isService && isExposed && (
                            <button
                                class="btn"
                                onClick={onUnexpose}
                                disabled={busy !== null}
                                title={`unexpose (removes ${targetingExposures.length} exposure${targetingExposures.length === 1 ? '' : 's'})`}
                            >unexpose</button>
                        )}
                        {httpUrl && (
                            <a
                                class="btn"
                                href={httpUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                style="text-decoration: none;"
                            >open ↗</a>
                        )}
                        <button
                            class="btn"
                            onClick={onRemove}
                            disabled={!canDelete || busy !== null}
                            style={canDelete ? 'color: var(--status-error); border-color: var(--status-error);' : ''}
                            title={canDelete ? '' : `not deletable (effective perms: ${effPerms || '***'})`}
                        >{busy === 'delete' ? 'removing…' : 'remove'}</button>
                    </>
                )}
            </div>

            {pickerOpen && (
                <TypePicker
                    parentId={node.id}
                    schemas={pickerSchemas}
                    onClose={() => setPickerOpen(false)}
                    onPick={onPickType}
                />
            )}

            {varPickerOpen && (
                <VarPicker
                    title={`link ${node.id} to…`}
                    nodes={allNodes}
                    exclude={descendantsOf(node.id)}
                    onClose={() => setVarPickerOpen(false)}
                    onPick={onPickLinkTarget}
                />
            )}
        </div>
    );
}
