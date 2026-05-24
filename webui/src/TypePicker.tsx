/**
 * Modal that lists every creatable type (nodes + meta-operations) and
 * lets the user pick one. On select it navigates to the new-node page
 * for that (parent, type) combination. The picker is agnostic to kind
 * — module is just another row.
 */
import { useEffect } from 'preact/hooks';
import type { NodeTypeSchema } from './api';

interface Props {
    parentId: string;
    schemas: Record<string, NodeTypeSchema>;
    onClose: () => void;
    onPick: (typeName: string) => void;
}

export function TypePicker({ parentId, schemas, onClose, onPick }: Props) {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    const entries = Object.values(schemas).sort((a, b) => a.type.localeCompare(b.type));

    return (
        <div class="modal-backdrop" onClick={onClose}>
            <div class="modal" onClick={(e: any) => e.stopPropagation()}>
                <div class="modal-header">
                    <div class="modal-title">add to {parentId}</div>
                    <button class="modal-close" onClick={onClose} aria-label="close">×</button>
                </div>
                <div class="modal-body">
                    {entries.length === 0 && (
                        <div class="empty-state">no types available</div>
                    )}
                    {entries.map(s => (
                        <button
                            key={s.type}
                            class="type-picker-row"
                            data-type={s.type}
                            onClick={() => onPick(s.type)}
                        >
                            <div class="type-picker-name">
                                {s.type}
                                <span class="type-picker-kind">{s.kind}</span>
                            </div>
                            {s.doc && <div class="type-picker-doc">{s.doc}</div>}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}
