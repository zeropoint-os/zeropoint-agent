/**
 * NewNodePage — the form for creating a node under a parent namespace
 * via a type schema. Reuses PropertyInspector in editable mode for the
 * form itself; the page just provides the chrome (header, save/cancel,
 * error display, post-save navigation).
 *
 * Dispatches by `schema.kind`:
 *   - "node":      wraps the form values into {id, type, config, parents, perms}
 *                  and POSTs to /api/dag/nodes (default endpoint for nodes).
 *   - "operation": POSTs the values verbatim to schema.endpoint, then
 *                  navigates to the resulting node if the response
 *                  includes one (e.g. module install).
 *
 * The id of a node-kind creation comes from {parent}/{name} where
 * `name` is read from the form's `name` field. If the schema doesn't
 * have a `name` field we surface a clear error instead of inventing
 * an id.
 */

import { useState } from 'preact/hooks';
import type { NodeTypeSchema } from './api';
import { PropertyInspector } from './PropertyInspector';

interface Props {
    parentId: string;
    schema: NodeTypeSchema;
    onCancel: () => void;
    onCreated: (newNodeId: string | null) => void;
}

export function NewNodePage({ parentId, schema, onCancel, onCreated }: Props) {
    // Seed values from schema defaults so widgets render something sensible.
    const initial: Record<string, any> = {};
    for (const f of schema.fields) {
        if ('default' in f) initial[f.name] = f.default;
    }
    if (schema.kind === 'node') {
        // Perms default comes from the schema; the inspector will pick
        // it up automatically, but include it here for visibility.
        initial.perms = schema.default_perms ?? '***';
    }

    const [values, setValues] = useState<Record<string, any>>(initial);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const onChange = (name: string, value: any) => {
        setValues(v => ({ ...v, [name]: value }));
    };

    const validate = (): string | null => {
        for (const f of schema.fields) {
            if (!f.required) continue;
            const v = values[f.name];
            if (v === undefined || v === null || v === '') {
                return `Required field '${f.name}' is empty`;
            }
        }
        if (schema.kind === 'node' && !('name' in values)) {
            return `Schema for ${schema.type} has no 'name' field; can't derive an id`;
        }
        // Node names become path segments in the graph id; restrict to a
        // sane identifier alphabet so the URL/router/store all stay happy.
        if (schema.kind === 'node') {
            const name = String(values.name ?? '');
            if (!/^[A-Za-z0-9_]+$/.test(name)) {
                return `Name must be a valid identifier (A-Z, a-z, 0-9, _); got ${JSON.stringify(name)}`;
            }
        }
        return null;
    };

    const onSave = async () => {
        const err = validate();
        if (err) { setError(err); return; }
        setBusy(true); setError(null);
        try {
            if (schema.kind === 'node') {
                await createNode(parentId, schema, values, onCreated);
            } else {
                await createOperation(parentId, schema, values, onCreated);
            }
        } catch (e: any) {
            setError(e.message || String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div class="detail">
            <div class="detail-header">
                <div class="detail-name">add {schema.type}</div>
            </div>
            <div class="detail-type">
                <span style="opacity: 0.6;">under {parentId} · </span>
                {schema.kind}
            </div>
            {schema.doc && (
                <div class="detail-section">
                    <div style="color: var(--fg-muted); font-size: 13px;">
                        {schema.doc}
                    </div>
                </div>
            )}

            {error && (
                <div class="detail-section">
                    <div class="detail-error">{error}</div>
                </div>
            )}

            <PropertyInspector
                schema={schema}
                editable
                values={values}
                onChange={onChange}
            />

            <div class="actions">
                <button
                    class="btn primary"
                    onClick={onSave}
                    disabled={busy}
                >{busy ? 'saving…' : 'save'}</button>
                <button
                    class="btn"
                    onClick={onCancel}
                    disabled={busy}
                >cancel</button>
            </div>
        </div>
    );
}

/** POST /api/dag/nodes with the form wrapped into a NodeSpec. */
async function createNode(
    parentId: string,
    schema: NodeTypeSchema,
    values: Record<string, any>,
    onCreated: (id: string | null) => void,
) {
    const name = String(values.name);
    const newId = parentId ? `${parentId}/${name}` : name;
    const { perms, ...config } = values;
    const body = {
        id: newId,
        type: schema.type,
        config,
        parents: parentId ? [parentId] : [],
        perms,
    };
    const r = await fetch('/api/dag/nodes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    onCreated(newId);
}

/** POST schema.endpoint with the form body verbatim. */
async function createOperation(
    parentId: string,
    schema: NodeTypeSchema,
    values: Record<string, any>,
    onCreated: (id: string | null) => void,
) {
    // Default parent_namespace from the route if the form didn't set it.
    const body: Record<string, any> = { ...values };
    if (parentId && !body.parent_namespace) {
        body.parent_namespace = parentId;
    }
    const r = await fetch(schema.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${r.status}`);
    }
    const result = await r.json();
    // Modules: navigate to the created namespace if present.
    onCreated(result.namespace_id ?? result.node_id ?? null);
}
