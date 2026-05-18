/**
 * PropertyInspector — schema-driven, recursive node property display.
 *
 * Read-mode only for now. The inspector composes its output from
 * three extensible sources:
 *
 *   1. Per-node-type **schema** (constructor params + descriptions),
 *      fetched from `GET /api/node-types`.
 *   2. **Standard fields** (path, perms, output, ...) registered in
 *      `widgets/standard-fields`. Anyone can add more without touching
 *      this file.
 *   3. Leftover `config` keys not in either — forward-compat with
 *      server-side schema drift.
 *
 * Each field's value is rendered by the **widget** registered for its
 * type name in `widgets/registry`. Object/list values use the
 * recursive ObjectWidget which collapses behind a chevron and itself
 * uses the inspector to render its contents — so the whole UI is one
 * uniform tree.
 *
 * Edit mode will reuse the same widgets with `readOnly=false` and an
 * `onChange` callback. Not implemented yet.
 */

import type { DagNode, NodeTypeSchema, FieldSchema } from './api';
import { getWidget } from './widgets/registry';
import { listStandardFields } from './widgets/standard-fields';
// Side-effect imports register the built-in widgets and fields. Any
// module that wants to extend the inspector imports/registers itself
// the same way.
import './widgets/builtin';

interface Props {
    node: DagNode;
    schema?: NodeTypeSchema;
}

interface Row {
    name: string;
    type: string;
    field?: FieldSchema;
    value: any;
}

function buildRows(node: DagNode, schema?: NodeTypeSchema): Row[] {
    const config = node.config || {};
    const seen = new Set<string>();
    const rows: Row[] = [];

    if (schema) {
        for (const f of schema.fields) {
            seen.add(f.name);
            rows.push({
                name: f.name,
                type: f.type,
                field: f,
                value: config[f.name],
            });
        }
    }

    for (const sf of listStandardFields()) {
        if (seen.has(sf.name)) continue;
        const value = sf.source(node);
        if (value === undefined) continue;
        if (value === null && (sf.name === 'error' || sf.name === 'output')) continue;
        seen.add(sf.name);
        rows.push({ name: sf.name, type: sf.type, value });
    }

    for (const [k, v] of Object.entries(config)) {
        if (seen.has(k)) continue;
        seen.add(k);
        rows.push({ name: k, type: 'any', value: v });
    }

    return rows;
}

export function PropertyInspector({ node, schema }: Props) {
    const rows = buildRows(node, schema);
    if (rows.length === 0) return null;

    return (
        <div class="detail-section">
            <div class="detail-section-title">properties</div>
            <FieldList rows={rows} />
        </div>
    );
}

/** Renders a list of rows (used by both PropertyInspector and ObjectWidget). */
export function FieldList({ rows }: { rows: Row[] }) {
    return (
        <>
            {rows.map(row => (
                <Field key={row.name} row={row} />
            ))}
        </>
    );
}

/** One row in the inspector: a key + a widget-rendered value. */
function Field({ row }: { row: Row }) {
    const W = getWidget(row.type);
    return (
        <div class="detail-field">
            <div class="detail-field-key">{row.name}</div>
            <div class="detail-field-value">
                <W value={row.value} schema={row.field} readOnly />
            </div>
        </div>
    );
}

/**
 * Build rows from an arbitrary object value. Used by ObjectWidget.
 * Public so other widgets/extensions can construct a recursive
 * inspector view.
 */
export function rowsFromObject(obj: Record<string, any>): Row[] {
    return Object.entries(obj).map(([k, v]) => ({
        name: k,
        type: inferType(v),
        value: v,
    }));
}

export function rowsFromList(arr: any[]): Row[] {
    return arr.map((v, i) => ({
        name: String(i),
        type: inferType(v),
        value: v,
    }));
}

function inferType(value: any): string {
    if (value === null || value === undefined) return 'string';
    if (typeof value === 'boolean') return 'bool';
    if (typeof value === 'number') return 'number';
    if (typeof value === 'string') return 'string';
    if (Array.isArray(value)) return 'list';
    if (typeof value === 'object') return 'dict';
    return 'any';
}

export type { Row };
