/**
 * Standard fields registry — universal node properties the inspector
 * always renders, regardless of node type.
 *
 * Each entry is `{ name, type, source }`. `name` is the field label
 * (and the key on the DagNode shape). `type` picks the widget. `source`
 * is a function that pulls the value off a DagNode — useful for
 * computed display values (e.g., a "summary" line) without changing
 * the wire format.
 *
 * Order matters: rows are rendered in registration order, after the
 * node's schema fields.
 *
 * To extend:
 *
 *     import { registerStandardField } from './widgets/standard-fields';
 *     registerStandardField({
 *         name: 'last_resolved',
 *         type: 'string',
 *         source: n => n.last_resolved_at,
 *     });
 */

import type { DagNode } from '../api';

export interface StandardField {
    name: string;
    type: string;
    source: (node: DagNode) => any;
}

const fields: StandardField[] = [];

export function registerStandardField(field: StandardField): void {
    // Replace any existing entry with the same name so callers can
    // override the built-in source/type.
    const idx = fields.findIndex(f => f.name === field.name);
    if (idx >= 0) fields[idx] = field;
    else fields.push(field);
}

export function listStandardFields(): ReadonlyArray<StandardField> {
    return fields;
}

// ---- built-ins -----------------------------------------------------------

registerStandardField({ name: 'path',            type: 'path',   source: n => n.path });
registerStandardField({ name: 'perms',           type: 'perms',  source: n => n.perms });
registerStandardField({ name: 'effective_perms', type: 'perms',  source: n => n.effective_perms });
registerStandardField({ name: 'parents',         type: 'list',   source: n => n.parents });
registerStandardField({ name: 'error',           type: 'string', source: n => n.error });
registerStandardField({ name: 'output',          type: 'any',    source: n => n.output });
