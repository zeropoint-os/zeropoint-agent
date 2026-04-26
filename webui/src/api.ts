/** API types matching the server responses. */

export interface DagNode {
    id: string;
    type: string;
    status: string;
    config: Record<string, any>;
    parents: string[];
    error: string | null;
    output?: Record<string, any>;
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

export async function buildDag(nodes: any[]): Promise<any> {
    const r = await fetch(`${BASE}/dag/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nodes }),
    });
    return r.json();
}


export const DEMO_GRAPH = [
    { id: "network", type: "network", config: { interface: "eth0" } },
    { id: "docker", type: "docker", config: {}, parents: ["network"] },
    { id: "nvidia", type: "driver", config: { driver: "nvidia", version: "535" } },
    { id: "disk-sda", type: "disk", config: { stable_id: "ata-QEMU_HARDDISK_QM00001" } },
    { id: "part-sda1", type: "partition", config: { number: 1, size_mb: 500 }, parents: ["disk-sda"] },
    { id: "fmt-sda1", type: "format", config: { filesystem: "ext4" }, parents: ["part-sda1"] },
    { id: "mnt-data", type: "mount", config: { mountpoint: "/mnt/data" }, parents: ["fmt-sda1"] },
    { id: "path-modules", type: "path", config: { path: "/mnt/data/modules" }, parents: ["mnt-data"] },
    { id: "ollama", type: "module", config: { source: "zp/ollama", module_id: "ollama" }, parents: ["path-modules"] },
    { id: "expose-ollama", type: "exposure", config: { module_id: "ollama", port: 11434 }, parents: ["ollama"] },
];

export async function loadDemoGraph(): Promise<any> {
    await buildDag(DEMO_GRAPH);
    return resolve("mock");
}
