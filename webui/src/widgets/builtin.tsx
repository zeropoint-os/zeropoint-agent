/**
 * Built-in widgets, registered against the widget registry on import.
 *
 * Each widget renders a single value for a single declared type.
 * Object/list widgets use the inspector's FieldList recursively so the
 * whole view is a uniform tree.
 */

import { useState } from 'preact/hooks';
import { registerWidget } from './registry';
import type { WidgetProps } from './registry';
import { FieldList, rowsFromObject, rowsFromList } from '../PropertyInspector';

// ---- primitives ----------------------------------------------------------

function StringWidget({ value }: WidgetProps) {
    if (value === null || value === undefined || value === '')
        return <span class="widget-empty">—</span>;
    return <span>{String(value)}</span>;
}

function NumberWidget({ value }: WidgetProps) {
    if (value === null || value === undefined || value === '')
        return <span class="widget-empty">—</span>;
    return <span>{Number(value)}</span>;
}

function BoolWidget({ value }: WidgetProps) {
    if (value === null || value === undefined)
        return <span class="widget-empty">—</span>;
    return <span>{value ? 'true' : 'false'}</span>;
}

// ---- collapsible object / list -----------------------------------------

/**
 * ObjectWidget — chevron-disclosure for nested objects.
 *
 * Collapsed shows a one-line summary. Expanded reuses FieldList so the
 * inner content is a recursive PropertyInspector view.
 */
function ObjectWidget({ value }: WidgetProps) {
    const [open, setOpen] = useState(false);
    if (value === null || value === undefined)
        return <span class="widget-empty">—</span>;
    if (typeof value !== 'object')
        return <StringWidget value={value} />;

    const isArr = Array.isArray(value);
    const entries = isArr
        ? (value as any[])
        : Object.entries(value as Record<string, any>);
    const count = entries.length;

    if (count === 0) return <span class="widget-empty">—</span>;

    const summary = isArr
        ? `[${count} item${count === 1 ? '' : 's'}]`
        : `{${count} field${count === 1 ? '' : 's'}}`;

    const rows = isArr
        ? rowsFromList(value as any[])
        : rowsFromObject(value as Record<string, any>);

    return (
        <div class="widget-object">
            <button
                type="button"
                class={`disclosure ${open ? 'open' : ''}`}
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
            >
                <span class="disclosure-chevron">{open ? '▾' : '▸'}</span>
                <span class="disclosure-summary">{summary}</span>
            </button>
            {open && (
                <div class="widget-object-body">
                    <FieldList rows={rows} />
                </div>
            )}
        </div>
    );
}

// ---- domain-specific widgets --------------------------------------------

/**
 * PermsWidget — three-character r/w/d permission string with each
 * position styled by its meaning (granted / wildcard / vetoed).
 */
function PermsWidget({ value }: WidgetProps) {
    const s = String(value ?? '');
    if (s.length !== 3) {
        return <span class="perms-value">{s || '—'}</span>;
    }
    const bits = ['r', 'w', 'd'];
    return (
        <span class="perms-value">
            {s.split('').map((ch, i) => {
                const cls = ch === '-' ? 'perms-veto'
                          : ch === '*' ? 'perms-wild'
                          : ch === bits[i] ? 'perms-grant'
                          : 'perms-other';
                return <span class={cls} key={i}>{ch}</span>;
            })}
        </span>
    );
}

/** PathWidget — namespace-derived path. Read-only by definition. */
function PathWidget({ value }: WidgetProps) {
    if (!value) return <span class="widget-empty">—</span>;
    return <span class="widget-path">{String(value)}</span>;
}

// ---- registration --------------------------------------------------------

registerWidget('string',  StringWidget);
registerWidget('number',  NumberWidget);
registerWidget('integer', NumberWidget);
registerWidget('bool',    BoolWidget);
registerWidget(['list', 'dict', 'any'], ObjectWidget);
registerWidget('perms',   PermsWidget);
registerWidget('path',    PathWidget);
