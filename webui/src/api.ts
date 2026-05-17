/** API types matching the server responses. */

export interface DagNode {
    id: string;
    type: string;
    status: string;
    config: Record<string, any>;
    parents: string[];
    error: string | null;
    output?: Record<string, any>;
    path?: string;              // namespace-derived path
    perms?: string;             // instance-level perms (3 chars, r/w/d/*/-)
    effective_perms?: string;   // resolved across all layers
}

export interface DagEdge {
    source: string;
    target: string;
}

export interface DagResponse {
    ok: boolean;
    nodes: DagNode[];
    edges: DagEdge[];
}

export interface HealthResponse {
    status: string;
    timestamp: string;
    mode: string;
    graph: { nodes: number; statuses: Record<string, number> };
}

export interface ResolveResponse {
    ok: boolean;
    mode: string;
    summary: Record<string, number>;
    nodes: DagNode[];
}

const BASE = '/api';

export async function fetchDag(): Promise<DagResponse> {
    const r = await fetch(`${BASE}/dag`);
    return r.json();
}

export async function fetchHealth(): Promise<HealthResponse> {
    const r = await fetch(`${BASE}/health`);
    return r.json();
}

export async function resolve(mode: string = 'mock'): Promise<ResolveResponse> {
    const r = await fetch(`${BASE}/dag/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
    });
    return r.json();
}
