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

/** Wrap an HTTP response into either { ok: data } or { error: detail }. */
async function asResult<T>(r: Response): Promise<{ ok?: T; error?: string; status: number }> {
    if (r.ok) return { ok: (await r.json()) as T, status: r.status };
    let detail = `HTTP ${r.status}`;
    try {
        const body = await r.json();
        if (body?.detail) detail = String(body.detail);
    } catch { /* ignore */ }
    return { error: detail, status: r.status };
}

/** PUT /api/dag/<id> with optional config and/or perms. */
export async function updateNode(
    id: string,
    body: { config?: Record<string, any>; perms?: string },
) {
    const r = await fetch(`${BASE}/dag/${encodePath(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return asResult<{ ok: boolean; node_id: string; invalidated: number }>(r);
}

/** DELETE /api/dag/<id>. */
export async function deleteNode(id: string) {
    const r = await fetch(`${BASE}/dag/${encodePath(id)}`, { method: 'DELETE' });
    return asResult<{ ok: boolean; removed: string[]; count: number }>(r);
}

/** Resolve a single node (or all if id is empty). */
export async function resolveNode(id: string, mode: string = 'live') {
    const url = id
        ? `${BASE}/dag/resolve/${encodePath(id)}`
        : `${BASE}/dag/resolve`;
    const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
    });
    return asResult<ResolveResponse>(r);
}

/** Link a Var to another Var. PUT /api/links/<id> body {target}. */
export async function linkVar(id: string, target: string) {
    const r = await fetch(`${BASE}/links/${encodePath(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
    });
    return asResult<{ ok: boolean; node_id: string; target: string | null }>(r);
}

/** Break a Var's existing link. PUT /api/links/<id> body {target: null}. */
export async function unlinkVar(id: string) {
    const r = await fetch(`${BASE}/links/${encodePath(id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: null }),
    });
    return asResult<{ ok: boolean; node_id: string; target: string | null }>(r);
}

/** Create a LAN-visible Exposure for a Service. POST /api/expose. */
export async function exposeService(service_id: string, opts?: { name?: string; host_port?: number }) {
    const r = await fetch(`${BASE}/expose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id, ...(opts || {}) }),
    });
    return asResult<{
        ok: boolean; exposure_id: string; name: string; protocol: string;
        host_port: number; service_id: string;
    }>(r);
}

/** Delete every Exposure under `service_id`. POST /api/unexpose. */
export async function unexposeService(service_id: string) {
    const r = await fetch(`${BASE}/unexpose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id }),
    });
    return asResult<{ ok: boolean; deleted: string[] }>(r);
}

/** Encode an id path, keeping the `/` separators readable. */
function encodePath(id: string): string {
    return id.split('/').map(encodeURIComponent).join('/');
}
