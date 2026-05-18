/**
 * Widget registry for the property inspector.
 *
 * A widget is a small Preact component that renders (and eventually
 * edits) one field value. Widgets are looked up by a string `type`
 * name, which comes from the server's per-node schema (or is hand-
 * coded for standard fields like "perms").
 *
 * To add a new widget type:
 *
 *     import { registerWidget } from './widgets/registry';
 *     registerWidget('color', ColorWidget);
 *
 * Schemas can use any `type` string; if a matching widget is
 * registered it's used, otherwise the inspector falls back to the
 * `'any'` widget (a recursive object/array renderer).
 *
 * Widgets all conform to `WidgetComponent` so the inspector can pass
 * them a uniform set of props. Edit-mode widgets will use `onChange`
 * and `readOnly`; today's read-only widgets just use `value`.
 */

import type { ComponentType } from 'preact';
import type { FieldSchema } from '../api';

export interface WidgetProps {
    value: any;
    schema?: FieldSchema;
    readOnly?: boolean;
    onChange?: (next: any) => void;
}

export type WidgetComponent = ComponentType<WidgetProps>;

const registry = new Map<string, WidgetComponent>();

/** Register a widget for one or more type names. Later registrations override earlier ones. */
export function registerWidget(type: string | string[], widget: WidgetComponent): void {
    const types = Array.isArray(type) ? type : [type];
    for (const t of types) registry.set(t, widget);
}

/** Look up the widget for a type name. Falls back to the 'any' widget if registered. */
export function getWidget(type: string): WidgetComponent {
    return registry.get(type)
        ?? registry.get('any')
        ?? FallbackWidget;
}

/** Final-fallback widget if no 'any' has been registered yet. */
const FallbackWidget: WidgetComponent = ({ value }) => (
    <span>{value === null || value === undefined ? '—' : String(value)}</span>
);

/** Used by the inspector to know which type names have widgets at all. */
export function hasWidget(type: string): boolean {
    return registry.has(type);
}
