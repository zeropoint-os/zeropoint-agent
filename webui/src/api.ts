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

/** Schema for a single field on a node class. */
export interface FieldSchema {
    name: string;
    type: string;             // "string" | "number" | "bool" | "list" | "dict" | "any" | ...
    required?: boolean;
    nullable?: boolean;
    default?: any;
    description?: string;
}

/** Schema for one creatable thing (node class or meta-operation). */
export interface NodeTypeSchema {
    type: string;             // short picker name
    kind: 'node' | 'operation';
    endpoint: string;
    fields: FieldSchema[];
    class_name?: string;      // only set for kind='node'
    module?: string;
    default_perms?: string;
    doc?: string;
}

export interface NodeTypesResponse {
    ok: boolean;
    types: Record<string, NodeTypeSchema>;  // keyed by short name
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

export async function fetchNodeTypes(): Promise<NodeTypesResponse> {
    const r = await fetch(`${BASE}/node-types`);
    return r.json();
}
