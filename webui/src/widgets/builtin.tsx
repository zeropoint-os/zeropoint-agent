/**
 * Built-in widgets, registered against the widget registry on import.
 *
 * Each widget honors both read and edit modes via `readOnly` /
 * `onChange`. Editable widgets are intentionally simple — primitives
 * are HTML inputs, perms is a 3-cycler, objects fall back to a JSON
 * textarea in edit mode (recursive editing comes later if needed).
 */

import { useState } from 'preact/hooks';
import { registerWidget } from './registry';
import type { WidgetProps } from './registry';
import { FieldList, rowsFromObject, rowsFromList } from '../PropertyInspector';

// ---- primitives ----------------------------------------------------------

function StringWidget({ value, readOnly, onChange }: WidgetProps) {
    if (readOnly) {
        if (value === null || value === undefined || value === '')
            return <span class="widget-empty">—</span>;
        return <span>{String(value)}</span>;
    }
    return (
        <input
            class="widget-input"
            type="text"
            value={value ?? ''}
            onInput={(e: any) => onChange?.(e.currentTarget.value)}
        />
    );
}

function NumberWidget({ value, readOnly, onChange }: WidgetProps) {
    if (readOnly) {
        if (value === null || value === undefined || value === '')
            return <span class="widget-empty">—</span>;
        return <span>{Number(value)}</span>;
    }
    return (
        <input
            class="widget-input"
            type="number"
            value={value ?? ''}
            onInput={(e: any) => {
                const raw = e.currentTarget.value;
                onChange?.(raw === '' ? null : Number(raw));
            }}
        />
    );
}

function BoolWidget({ value, readOnly, onChange }: WidgetProps) {
    if (readOnly) {
        if (value === null || value === undefined)
            return <span class="widget-empty">—</span>;
        return <span>{value ? 'true' : 'false'}</span>;
    }
    return (
        <label class="widget-checkbox">
            <input
                type="checkbox"
                checked={!!value}
                onChange={(e: any) => onChange?.(e.currentTarget.checked)}
            />
            <span>{value ? 'true' : 'false'}</span>
        </label>
    );
}

// ---- collapsible object / list -----------------------------------------

/**
 * ObjectWidget — chevron-disclosure for nested objects. In read mode
 * the contents recurse through FieldList. In edit mode we fall back
 * to a JSON textarea so the user can freely edit nested shapes; this
 * is the path for `dict`/`list` config fields like a module's
 * `overrides`. Recursive editing can be added later if it becomes
 * common.
 */
function ObjectWidget({ value, readOnly, onChange }: WidgetProps) {
    const [open, setOpen] = useState(!readOnly);
    if (readOnly) {
        if (value === null || value === undefined)
            return <span class="widget-empty">—</span>;
        if (typeof value !== 'object')
            return <StringWidget value={value} readOnly />;

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

    // Edit mode: JSON textarea.
    const text = value === null || value === undefined
        ? ''
        : JSON.stringify(value, null, 2);
    return (
        <textarea
            class="widget-input widget-textarea"
            rows={5}
            value={text}
            placeholder='{"key": "value"}'
            onInput={(e: any) => {
                const raw = e.currentTarget.value;
                if (raw.trim() === '') { onChange?.(null); return; }
                try {
                    onChange?.(JSON.parse(raw));
                } catch {
                    // While the user is typing, silently keep the
                    // last valid value; commit when JSON parses.
                }
            }}
        />
    );
}

// ---- domain-specific widgets --------------------------------------------

const PERM_CYCLES: Record<number, string[]> = {
    0: ['r', '-', '*'],
    1: ['w', '-', '*'],
    2: ['d', '-', '*'],
};

/**
 * PermsWidget — three characters, each cycles through its valid set
 * (`r-*`, `w-*`, `d-*`) on click in edit mode. Click colors mark
 * grant/wildcard/veto per position.
 */
function PermsWidget({ value, readOnly, onChange }: WidgetProps) {
    let s = String(value ?? '');
    if (s.length !== 3) s = '***';

    const bits = ['r', 'w', 'd'];
    const cycle = (i: number) => {
        const opts = PERM_CYCLES[i];
        const cur = s[i];
        const next = opts[(opts.indexOf(cur) + 1) % opts.length] ?? opts[0];
        onChange?.(s.slice(0, i) + next + s.slice(i + 1));
    };

    return (
        <span class="perms-value">
            {s.split('').map((ch, i) => {
                const cls = ch === '-' ? 'perms-veto'
                          : ch === '*' ? 'perms-wild'
                          : ch === bits[i] ? 'perms-grant'
                          : 'perms-other';
                if (readOnly) return <span class={cls} key={i}>{ch}</span>;
                return (
                    <button
                        type="button"
                        key={i}
                        class={`perms-cycler ${cls}`}
                        onClick={() => cycle(i)}
                        aria-label={`cycle ${bits[i]} bit`}
                    >
                        {ch}
                    </button>
                );
            })}
        </span>
    );
}

/** PathWidget — namespace-derived; always read-only. */
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
