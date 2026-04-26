import { useState, useEffect } from 'preact/hooks';
import type { DagNode, DagEdge, HealthResponse } from './api';
import { fetchDag, fetchHealth, resolve, loadDemoGraph } from './api';
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

function buildPath(targetId: string, edges: DagEdge[]): string[] {
    const parentMap = new Map<string, string>();
    for (const e of edges) {
        if (!parentMap.has(e.target)) parentMap.set(e.target, e.source);
    }
    const path: string[] = [];
    let current: string | undefined = targetId;
    while (current) {
        path.unshift(current);
        current = parentMap.get(current);
    }
    return path;
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

    // Hash-based routing: read node ID from URL hash
    const getHashId = (): string | null => {
        const hash = window.location.hash.replace(/^#\/?/, '');
        return hash || null;
    };

    const [currentId, setCurrentId] = useState<string | null>(getHashId);

    // Apply theme
    useEffect(() => {
        document.documentElement.classList.toggle('dark', isDark);
        localStorage.setItem('zp-theme', isDark ? 'dark' : 'light');
    }, [isDark]);

    // Sync hash → state on popstate (back/forward)
    useEffect(() => {
        const onHashChange = () => setCurrentId(getHashId());
        window.addEventListener('hashchange', onHashChange);
        return () => window.removeEventListener('hashchange', onHashChange);
    }, []);

    const navigate = (id: string | null) => {
        setCurrentId(id);
        window.location.hash = id ? `/${id}` : '/';
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

    // Pivot path
    const pivotPath = currentId ? buildPath(currentId, edges) : [];

    // Siblings at current level
    const currentParents = edges.filter(e => e.target === currentId).map(e => e.source);
    const siblings = currentParents.length > 0
        ? (cm.get(currentParents[0]) || [])
        : roots;

    const handleResolve = async () => {
        const mode = health?.mode || 'mock';
        await resolve(mode);
        await load();
    };

    const handleLoadDemo = async () => {
        await loadDemoGraph();
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
                <div class="content" style="display: flex; align-items: center; justify-content: center; flex: 1;">
                    <div style="text-align: center; color: var(--fg-dim);">
                        <div style="font-size: 18px; font-weight: 300; margin-bottom: 12px;">
                            no nodes
                        </div>
                        <button class="btn primary" onClick={handleLoadDemo}>
                            load demo graph
                        </button>
                    </div>
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

            {/* Pivot — home or path through graph */}
            <div class="pivot">
                <button
                    class={`pivot-tab ${isHome ? 'active' : ''}`}
                    onClick={() => navigate(null)}
                >
                    home
                </button>
                {pivotPath.map(id => (
                    <button
                        key={id}
                        class={`pivot-tab ${id === currentId ? 'active' : ''}`}
                        onClick={() => navigate(id)}
                    >
                        {id}
                    </button>
                ))}
                {/* Siblings not in path */}
                {!isHome && siblings
                    .filter(id => id !== currentId && !pivotPath.includes(id))
                    .map(id => (
                        <button
                            key={id}
                            class="pivot-tab"
                            onClick={() => navigate(id)}
                        >
                            {id}
                        </button>
                    ))}
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
