import { useState, useEffect } from 'preact/hooks';
import type { DagNode, DagEdge, HealthResponse } from './api';
import { fetchDag, fetchHealth, resolve, loadDemoGraph } from './api';
import { NodeList } from './NodeList';
import { NodeDetail } from './NodeDetail';

/** Build a depth map from edges for indentation. */
function buildDepthMap(nodes: DagNode[], edges: DagEdge[]): Map<string, number> {
    const children = new Map<string, string[]>();
    const parentSet = new Set<string>();
    for (const e of edges) {
        const list = children.get(e.source) || [];
        list.push(e.target);
        children.set(e.source, list);
        parentSet.add(e.target);
    }

    const roots = nodes.filter(n => !parentSet.has(n.id)).map(n => n.id);
    const depths = new Map<string, number>();

    const walk = (id: string, depth: number) => {
        if (depths.has(id)) return;
        depths.set(id, depth);
        for (const child of children.get(id) || []) {
            walk(child, depth + 1);
        }
    };
    for (const r of roots) walk(r, 0);
    return depths;
}

/** Order nodes by topo sort (parents before children). */
function topoOrder(nodes: DagNode[], edges: DagEdge[]): DagNode[] {
    const depths = buildDepthMap(nodes, edges);
    const nodeMap = new Map(nodes.map(n => [n.id, n]));

    // Walk depth-first from roots preserving chain order
    const parentSet = new Set(edges.map(e => e.target));
    const roots = nodes.filter(n => !parentSet.has(n.id));
    const children = new Map<string, string[]>();
    for (const e of edges) {
        const list = children.get(e.source) || [];
        list.push(e.target);
        children.set(e.source, list);
    }

    const ordered: DagNode[] = [];
    const visited = new Set<string>();
    const walk = (id: string) => {
        if (visited.has(id)) return;
        visited.add(id);
        const node = nodeMap.get(id);
        if (node) ordered.push(node);
        for (const child of children.get(id) || []) {
            walk(child);
        }
    };
    for (const r of roots) walk(r.id);

    // Add any unvisited nodes
    for (const n of nodes) {
        if (!visited.has(n.id)) ordered.push(n);
    }
    return ordered;
}

export function App() {
    const [nodes, setNodes] = useState<DagNode[]>([]);
    const [edges, setEdges] = useState<DagEdge[]>([]);
    const [health, setHealth] = useState<HealthResponse | null>(null);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [showDetail, setShowDetail] = useState(false);

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

    const ordered = topoOrder(nodes, edges);
    const depths = buildDepthMap(nodes, edges);
    const selected = nodes.find(n => n.id === selectedId) || null;

    const handleSelect = (id: string) => {
        setSelectedId(id);
        setShowDetail(true);
    };

    const handleBack = () => {
        setShowDetail(false);
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

    const handleNavigate = (id: string) => {
        setSelectedId(id);
        setShowDetail(true);
    };

    const statusSummary = health?.graph?.statuses || {};
    const total = health?.graph?.nodes || 0;
    const mode = health?.mode || 'mock';

    return (
        <div class="app">
            <div class="title">zeropo<span style="color: var(--fg-dim)">int</span></div>
            <div class="subtitle">
                {total > 0
                    ? `${total} nodes — ${Object.entries(statusSummary).map(([s, c]) => `${c} ${s.replace('_', ' ')}`).join(', ')}`
                    : 'no graph loaded'}
            </div>

            <div class="content">
                <div class="master" style={showDetail && window.innerWidth < 768 ? 'display:none' : ''}>
                    <NodeList
                        nodes={ordered}
                        depths={depths}
                        selectedId={selectedId}
                        onSelect={handleSelect}
                        onLoadDemo={handleLoadDemo}
                    />
                </div>

                {selected && showDetail && (
                    <div class="detail-panel">
                        {window.innerWidth < 768 && (
                            <div class="detail-back" onClick={handleBack}>
                                ← back
                            </div>
                        )}
                        <NodeDetail
                            node={selected}
                            allNodes={nodes}
                            edges={edges}
                            onNavigate={handleNavigate}
                        />
                    </div>
                )}

                {!selected && !showDetail && window.innerWidth >= 768 && (
                    <div class="detail-panel">
                        <div class="detail">
                            <div class="detail-name" style="color: var(--fg-dim)">
                                select a node
                            </div>
                        </div>
                    </div>
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
