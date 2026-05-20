/**
 * PropertyInspector — schema-driven, recursive node property display
 * and editor.
 *
 * **Read mode** (`editable` unset): renders schema fields + standard
 * fields (path, perms, output, ...) + leftover config keys, all read-
 * only. Used by the node detail view.
 *
 * **Edit mode** (`editable` set + `values` + `onChange`): renders only
 * schema fields plus the `perms` row, controlled by `values`. Standard
 * runtime/derived fields (path, effective_perms, output, error, parents)
 * are hidden because they're not user-settable. Used by the new-node
 * page and (later) the edit-node flow.
 */

import type { DagNode, NodeTypeSchema, FieldSchema } from './api';
import { getWidget } from './widgets/registry';
import { listStandardFields } from './widgets/standard-fields';
// Side-effect imports register the built-in widgets and fields.
import './widgets/builtin';
import { useState } from 'preact/hooks';

interface Props {
    /** The node being inspected. Required in read mode; optional in edit mode. */
    node?: DagNode;
    /** Schema for the node's type. Drives widget selection and (in edit mode) the field list. */
    schema?: NodeTypeSchema;
    /** When true, fields are rendered as editable widgets. */
    editable?: boolean;
    /** In edit mode, the current field values (controlled). */
    values?: Record<string, any>;
    /** In edit mode, fired when a field changes. */
    onChange?: (name: string, value: any) => void;
}

interface Row {
    name: string;
    type: string;
    field?: FieldSchema;
    value: any;
    /** Edit-mode setter for this row, if editable. */
    onChange?: (next: any) => void;
}

/** Build rows for read mode: schema + standard fields + leftover config. */
function readRows(node: DagNode, schema?: NodeTypeSchema): Row[] {
    const config = node.config || {};
    const seen = new Set<string>();
    const rows: Row[] = [];

    if (schema) {
        for (const f of schema.fields) {
            seen.add(f.name);
            rows.push({ name: f.name, type: f.type, field: f, value: config[f.name] });
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

/** Build rows for edit mode: schema fields + perms, all editable. */
function editRows(
    schema: NodeTypeSchema | undefined,
    values: Record<string, any>,
    onChange: (name: string, value: any) => void,
): Row[] {
    const rows: Row[] = [];
    if (schema) {
        for (const f of schema.fields) {
            rows.push({
                name: f.name,
                type: f.type,
                field: f,
                value: values[f.name] ?? f.default ?? null,
                onChange: (next) => onChange(f.name, next),
            });
        }
    }
    // perms is special: every node has one, regardless of schema.
    // Only include for node-kind schemas (operations don't take perms).
    if (!schema || schema.kind === 'node') {
        rows.push({
            name: 'perms',
            type: 'perms',
            value: values.perms ?? schema?.default_perms ?? '***',
            onChange: (next) => onChange('perms', next),
        });
    }
    return rows;
}

export function PropertyInspector(props: Props) {
    const { node, schema, editable, values, onChange } = props;
    let rows: Row[];
    if (editable) {
        if (!values || !onChange) {
            throw new Error('PropertyInspector: editable mode requires values and onChange');
        }
        rows = editRows(schema, values, onChange);
    } else {
        if (!node) {
            throw new Error('PropertyInspector: read mode requires a node');
        }
        rows = readRows(node, schema);
    }

    if (rows.length === 0) return null;

    return (
        <div class="detail-section">
            <div class="detail-section-title">properties</div>
            <FieldList rows={rows} editable={editable} />
        </div>
    );
}

/** Renders a list of rows; used recursively by ObjectWidget too. */
export function FieldList({ rows, editable }: { rows: Row[]; editable?: boolean }) {
    return (
        <>
            {rows.map(row => (
                <Field key={row.name} row={row} editable={editable} />
            ))}
        </>
    );
}

function Field({ row, editable }: { row: Row; editable?: boolean }) {
    const isEditable = editable && !!row.onChange;

    // In read mode, object/list rows render as a single-column
    // collapsible: chevron + name + summary on top, indented body below.
    // This keeps the chevron next to the name (not the value) and gives
    // each nesting level a small consistent indent regardless of depth.
    const isCollapsible = !isEditable
        && (row.type === 'list' || row.type === 'dict' || row.type === 'any')
        && row.value !== null
        && row.value !== undefined
        && typeof row.value === 'object'
        && (Array.isArray(row.value)
            ? row.value.length > 0
            : Object.keys(row.value).length > 0);

    if (isCollapsible) {
        return <CollapsibleField row={row} />;
    }

    const W = getWidget(row.type);
    return (
        <div class="detail-field">
            <div class="detail-field-key">
                {row.name}
                {row.field?.required && isEditable && (
                    <span class="detail-field-required" title="required">*</span>
                )}
            </div>
            <div class="detail-field-value">
                <W
                    value={row.value}
                    schema={row.field}
                    readOnly={!isEditable}
                    onChange={row.onChange}
                />
            </div>
        </div>
    );
}

function CollapsibleField({ row }: { row: Row }) {
    const value = row.value;
    const isArr = Array.isArray(value);
    const count = isArr
        ? (value as any[]).length
        : Object.keys(value as Record<string, any>).length;
    const summary = isArr
        ? `[${count} item${count === 1 ? '' : 's'}]`
        : `{${count} field${count === 1 ? '' : 's'}}`;
    const nestedRows = isArr
        ? rowsFromList(value as any[])
        : rowsFromObject(value as Record<string, any>);

    return (
        <CollapsibleRow name={row.name} summary={summary} rows={nestedRows} />
    );
}

export function rowsFromObject(obj: Record<string, any>): Row[] {
    return Object.entries(obj).map(([k, v]) => ({
        name: k, type: inferType(v), value: v,
    }));
}

/** Reusable collapsible row: chevron + name + summary, body indented underneath. */
export function CollapsibleRow({
    name, summary, rows,
}: { name: string; summary: string; rows: Row[] }) {
    const [open, setOpen] = useState(false);
    return (
        <div class="detail-field-collapsible">
            <button
                type="button"
                class={`disclosure ${open ? 'open' : ''}`}
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
            >
                <span class="disclosure-chevron">{open ? '▾' : '▸'}</span>
                <span class="disclosure-name">{name}</span>
                {!open && <span class="disclosure-summary">{summary}</span>}
            </button>
            {open && (
                <div class="nested-body">
                    <FieldList rows={rows} />
                </div>
            )}
        </div>
    );
}

export function rowsFromList(arr: any[]): Row[] {
    return arr.map((v, i) => ({
        name: String(i), type: inferType(v), value: v,
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
