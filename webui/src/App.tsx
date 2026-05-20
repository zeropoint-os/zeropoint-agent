import { useState, useEffect } from 'preact/hooks';
import type { DagNode, DagEdge, HealthResponse, NodeTypeSchema } from './api';
import { fetchDag, fetchHealth, fetchNodeTypes, resolve } from './api';
import { NodeDetail } from './NodeDetail';
import { Tile } from './Tile';

function childrenMap(edges: DagEdge[]): Map<string, string[]> {
    const m = new Map<string, string[]>();
    for (const e of edges) {
        const list = m.get(e.source) || [];
        list.push(e.target);
        m.set(e.source, list);
    }
    return m;
}

function findRoots(nodes: DagNode[], edges: DagEdge[]): string[] {
    const hasParent = new Set(edges.map(e => e.target));
    return nodes.filter(n => !hasParent.has(n.id)).map(n => n.id);
}

export function App() {
    const [nodes, setNodes] = useState<DagNode[]>([]);
    const [edges, setEdges] = useState<DagEdge[]>([]);
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [isDark, setIsDark] = useState(() => {
        const saved = localStorage.getItem('zp-theme');
        if (saved) return saved === 'dark';
        return window.matchMedia('(prefers-color-scheme: dark)').matches;
    });

    // Navigation stack — the path the user actually walked through. This is
    // the source of truth for the breadcrumb. It is NOT derived from edges
    // (which would pick arbitrary parents when a node has multiple).
    const getHashId = (): string | null => {
        const hash = window.location.hash.replace(/^#\/?/, '');
        return hash || null;
    };

    const [navStack, setNavStack] = useState<string[]>(() => {
        const id = getHashId();
        return id ? [id] : [];
    });
    const currentId = navStack.length > 0 ? navStack[navStack.length - 1] : null;

    // Type schemas, fetched once. Indexed two ways:
    //   - schemasByPickerName: short name → schema (for the type picker)
    //   - schemasByClassName:  class name → schema (for matching DagNode.type)
    const [schemasByPickerName, setSchemasByPickerName] =
        useState<Record<string, NodeTypeSchema>>({});
    const [schemasByClassName, setSchemasByClassName] =
        useState<Record<string, NodeTypeSchema>>({});
    useEffect(() => {
        fetchNodeTypes()
            .then(r => {
                const byPicker = r.types || {};
                const byClass: Record<string, NodeTypeSchema> = {};
                for (const s of Object.values(byPicker)) {
                    if (s.class_name) byClass[s.class_name] = s;
                }
                setSchemasByPickerName(byPicker);
                setSchemasByClassName(byClass);
            })
            .catch(e => console.error('Failed to load type schemas:', e));
    }, []);

    // Apply theme
    useEffect(() => {
        document.documentElement.classList.toggle('dark', isDark);
        localStorage.setItem('zp-theme', isDark ? 'dark' : 'light');
    }, [isDark]);

    // Sync hash → state on popstate (back/forward, deep link)
    useEffect(() => {
        const onHashChange = () => {
            const id = getHashId();
            setNavStack(stack => {
                if (id === null) return [];
                // If the id is already in the stack, slice to it (back navigation).
                const idx = stack.indexOf(id);
                if (idx >= 0) return stack.slice(0, idx + 1);
                // Otherwise treat as a deep link — start a fresh stack with just this id.
                return [id];
            });
        };
        window.addEventListener('hashchange', onHashChange);
        return () => window.removeEventListener('hashchange', onHashChange);
    }, []);

    // Navigate forward (push) or back (slice). Updates hash; hashchange
    // listener keeps the stack in sync.
    const navigate = (id: string | null) => {
        setNavStack(stack => {
            let next: string[];
            if (id === null) {
                next = [];
            } else {
                const idx = stack.indexOf(id);
                if (idx >= 0) {
                    next = stack.slice(0, idx + 1);
                } else {
                    next = [...stack, id];
                }
            }
            // Keep hash in sync.
            const newHash = next.length > 0 ? `/${next[next.length - 1]}` : '/';
            if (window.location.hash !== `#${newHash}`) {
                window.location.hash = newHash;
            }
            return next;
        });
    };

    const load = async () => {
        try {
            const [dag, h] = await Promise.all([fetchDag(), fetchHealth()]);
            setNodes(dag.nodes || []);
            setEdges(dag.edges || []);
            setHealth(h);
        } catch (e) {
            console.error('Failed to load DAG:', e);
        }
    };

    useEffect(() => {
        load();
        const interval = setInterval(load, 5000);
        return () => clearInterval(interval);
    }, []);

    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const cm = childrenMap(edges);
    const roots = findRoots(nodes, edges);
    const current = currentId ? nodeMap.get(currentId) || null : null;
    const isHome = currentId === null;

    // Pivot path = the user's navigation stack (no graph traversal).
    const pivotPath = navStack;

    // Siblings at current level — children of the previous navigation step
    // (or roots if we're at the top of the stack).
    const previousId = navStack.length >= 2 ? navStack[navStack.length - 2] : null;
    const siblings = previousId ? (cm.get(previousId) || []) : roots;

    const handleResolve = async () => {
        const mode = health?.mode || 'mock';
        await resolve(mode);
        await load();
    };

    const total = health?.graph?.nodes || 0;
    const mode = health?.mode || 'mock';
    const statusSummary = health?.graph?.statuses || {};

    // Empty state
    if (nodes.length === 0) {
        return (
            <div class="app">
                <div class="title-bar">
                    <div class="title">zeropoint</div>
                    <button class="theme-toggle" onClick={() => setIsDark(!isDark)}>
                        {isDark ? '☀' : '☾'}
                    </button>
                </div>
                <div class="empty-state">
                    <div class="empty-state-title">no nodes</div>
                </div>
                <div class="status-bar">
                    <span>0 nodes</span>
                    <span class="mode">{mode}</span>
                </div>
            </div>
        );
    }

    return (
        <div class="app">
            <div class="title-bar">
                <div class="title">zeropoint</div>
                <button class="theme-toggle" onClick={() => setIsDark(!isDark)}>
                    {isDark ? '☀' : '☾'}
                </button>
            </div>
            <div class="subtitle">
                {Object.entries(statusSummary).map(([s, c]) => `${c} ${s.replace('_', ' ')}`).join(' · ')}
            </div>

            {/* Pivot — the user's navigation stack (push on click-into,
                slice on click-on-crumb). Not derived from the graph. */}
            <div class="pivot">
                <button
                    class={`pivot-tab ${isHome ? 'active' : ''}`}
                    onClick={() => navigate(null)}
                >
                    home
                </button>
                {pivotPath.map((id, i) => {
                    const prev = i > 0 ? pivotPath[i - 1] : null;
                    const label = prev && id.startsWith(prev + '/')
                        ? id.slice(prev.length + 1)
                        : id;
                    return (
                        <button
                            key={id}
                            class={`pivot-tab ${id === currentId ? 'active' : ''}`}
                            onClick={() => navigate(id)}
                        >
                            {label}
                        </button>
                    );
                })}
                {/* Siblings of the current node, not yet visited. */}
                {!isHome && siblings
                    .filter(id => id !== currentId && !pivotPath.includes(id))
                    .map(id => {
                        const label = previousId && id.startsWith(previousId + '/')
                            ? id.slice(previousId.length + 1)
                            : id;
                        return (
                            <button
                                key={id}
                                class="pivot-tab"
                                onClick={() => navigate(id)}
                            >
                                {label}
                            </button>
                        );
                    })}
            </div>

            <div class="content">
                {/* Home: live tiles for root nodes */}
                {isHome && (
                    <div class="tiles">
                        {roots.map(rootId => {
                            const node = nodeMap.get(rootId);
                            if (!node) return null;
                            const descendants = countDescendants(rootId, cm);
                            return (
                                <Tile
                                    key={rootId}
                                    node={node}
                                    onClick={() => navigate(rootId)}
                                    childCount={descendants}
                                />
                            );
                        })}
                    </div>
                )}

                {/* Node detail + children tiles */}
                {current && (
                    <NodeDetail
                        node={current}
                        allNodes={nodes}
                        edges={edges}
                        onNavigate={(id) => navigate(id)}
                        schema={schemasByClassName[current.type]}
                        pickerSchemas={schemasByPickerName}
                    />
                )}
            </div>

            <div class="status-bar">
                <span>{total} nodes</span>
                <span class="mode">{mode}</span>
                <button class="btn primary" onClick={handleResolve}>⚡ resolve</button>
            </div>
        </div>
    );
}

function countDescendants(id: string, cm: Map<string, string[]>): number {
    let count = 0;
    const queue = cm.get(id) || [];
    const visited = new Set<string>();
    while (queue.length > 0) {
        const child = queue.shift()!;
        if (visited.has(child)) continue;
        visited.add(child);
        count++;
        queue.push(...(cm.get(child) || []));
    }
    return count;
}
