/**
 * VarPicker — modal for picking a Var to link to.
 *
 * Renders the graph's Vars as a flat list (grouped/sorted by namespace
 * path) with a filter textbox. Picking calls `onPick(id)`; closing
 * without picking calls `onClose()`.
 *
 * Filtering is client-side: substring match against the var's full path.
 * The `exclude` set (typically the source var itself + its descendants)
 * is removed up-front so cycles can't be picked. Server still validates
 * — this is purely UX.
 *
 * A "Var" for picker purposes is any node whose `type` is one of the
 * Var-family classes. The list comes from props (the caller already
 * has the full graph).
 */

import { useEffect, useMemo, useState } from 'preact/hooks';
import type { DagNode } from './api';

const VAR_TYPES = new Set(['Var', 'NamespacedVar', 'OutputVar', 'DirectoryVar']);

interface Props {
    title?: string;
    nodes: DagNode[];
    exclude: Set<string>;
    onPick: (id: string) => void;
    onClose: () => void;
}

export function VarPicker({ title = 'link to var…', nodes, exclude, onPick, onClose }: Props) {
    const [filter, setFilter] = useState('');

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    const candidates = useMemo(() => {
        const out = nodes.filter(n =>
            VAR_TYPES.has(n.type)
            && !exclude.has(n.id)
        );
        out.sort((a, b) => a.id.localeCompare(b.id));
        return out;
    }, [nodes, exclude]);

    const filtered = useMemo(() => {
        const f = filter.trim().toLowerCase();
        if (!f) return candidates;
        return candidates.filter(n => n.id.toLowerCase().includes(f));
    }, [candidates, filter]);

    return (
        <div class="modal-backdrop" onClick={onClose}>
            <div class="modal" onClick={(e: any) => e.stopPropagation()}>
                <div class="modal-header">
                    <div class="modal-title">{title}</div>
                    <button class="modal-close" onClick={onClose} aria-label="close">×</button>
                </div>
                <div class="modal-body">
                    <input
                        class="widget-input modal-filter"
                        type="text"
                        placeholder="filter by name…"
                        value={filter}
                        onInput={(e: any) => setFilter(e.currentTarget.value)}
                        // Pre-focus so the user can start typing immediately.
                        ref={(el: HTMLInputElement | null) => el?.focus()}
                    />

                    {filtered.length === 0 && (
                        <div class="empty-state" style="padding: 24px 16px;">
                            no matching vars
                        </div>
                    )}

                    {filtered.map(n => (
                        <button
                            key={n.id}
                            class="type-picker-row"
                            onClick={() => onPick(n.id)}
                        >
                            <div class="type-picker-name">
                                {n.id}
                                <span class="type-picker-kind">{n.type}</span>
                            </div>
                            {n.output && typeof (n.output as any).value !== 'undefined' && (
                                <div class="type-picker-doc">
                                    current value: {String((n.output as any).value).slice(0, 80)}
                                </div>
                            )}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}
