import { useState, useEffect } from 'preact/hooks';
import type { DagNode, DagEdge, HealthResponse } from './api';
import { fetchDag, fetchHealth, resolve, loadDemoGraph } from './api';
import { NodeDetail } from './NodeDetail';

/** Build parent→children map from edges. */
function childrenMap(edges: DagEdge[]): Map<string, string[]> {
    const m = new Map<string, string[]>();
    for (const e of edges) {
        const list = m.get(e.source) || [];
        list.push(e.target);
        m.set(e.source, list);
    }
    return m;
}

/** Find root node IDs (no incoming edges). */
function findRoots(nodes: DagNode[], edges: DagEdge[]): string[] {
    const hasParent = new Set(edges.map(e => e.target));
    return nodes.filter(n => !hasParent.has(n.id)).map(n => n.id);
}

/** Build path from a root to the given node. */
function buildPath(targetId: string, edges: DagEdge[], nodeMap: Map<string, DagNode>): string[] {
    const parentMap = new Map<string, string>();
    for (const e of edges) {
        // First parent wins (for path building)
        if (!parentMap.has(e.target)) {
            parentMap.set(e.target, e.source);
        }
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
    const [currentId, setCurrentId] = useState<string | null>(null);

    const load = async () => {
        try {
            const [dag, h] = await Promise.all([fetchDag(), fetchHealth()]);
            setNodes(dag.nodes || []);
            setEdges(dag.edges || []);
            setHealth(h);
            // Auto-select first root if nothing selected
            if (!currentId && dag.nodes?.length > 0) {
                const roots = findRoots(dag.nodes, dag.edges || []);
                if (roots.length > 0) setCurrentId(roots[0]);
            }
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
    const children = childrenMap(edges);
    const roots = findRoots(nodes, edges);
    const current = currentId ? nodeMap.get(currentId) || null : null;

    // Build the pivot path from root to current node
    const pivotPath = currentId ? buildPath(currentId, edges, nodeMap) : [];

    // Siblings: nodes at the same level (same parent, or all roots)
    const currentParents = edges.filter(e => e.target === currentId).map(e => e.source);
    const siblings = currentParents.length > 0
        ? (children.get(currentParents[0]) || [])
        : roots;

    const handleNavigate = (id: string) => {
        setCurrentId(id);
    };

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
                <div class="title">zeropo<span style="color: var(--fg-dim)">int</span></div>
                <div class="content" style="display: flex; align-items: center; justify-content: center; flex: 1;">
                    <div style="text-align: center; color: var(--fg-dim);">
                        <div style="font-size: 18px; font-weight: 300; margin-bottom: 12px;">
                            no nodes
                        </div>
                        <div style="font-size: 13px; margin-bottom: 24px;">
                            build a graph via the API or load a demo
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
            <div class="title">zeropo<span style="color: var(--fg-dim)">int</span></div>
            <div class="subtitle">
                {Object.entries(statusSummary).map(([s, c]) => `${c} ${s.replace('_', ' ')}`).join(' · ')}
            </div>

            {/* Pivot breadcrumb — path from root to current */}
            <div class="pivot">
                {pivotPath.map((id, i) => (
                    <button
                        key={id}
                        class={`pivot-tab ${id === currentId ? 'active' : ''}`}
                        onClick={() => handleNavigate(id)}
                    >
                        {id}
                    </button>
                ))}

                {/* Show siblings of current node that aren't in the path */}
                {siblings
                    .filter(id => id !== currentId && !pivotPath.includes(id))
                    .map(id => (
                        <button
                            key={id}
                            class="pivot-tab"
                            onClick={() => handleNavigate(id)}
                        >
                            {id}
                        </button>
                    ))}
            </div>

            {/* Current node's card */}
            <div class="content">
                {current && (
                    <NodeDetail
                        node={current}
                        allNodes={nodes}
                        edges={edges}
                        onNavigate={handleNavigate}
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
