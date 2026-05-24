/**
 * Expose / Unexpose flow — the xDS milestone.
 *
 * The agent runs in mock mode with ZEROPOINT_XDS_FORCE=1 so the xDS
 * server starts and reconcile runs, but no real Envoy container is
 * spawned. We exercise:
 *
 *   1. Install echo, resolve once → per-port OutputVar `port_placeholder`
 *      is synced into the graph.
 *   2. Navigate to the port node → "expose" button is visible.
 *   3. Click expose → an Endpoint appears at modules/echo/endpoint_*.
 *   4. The port node now shows "unexpose" (and only one endpoint targets it).
 *   5. Endpoint detail shows "Open" link for http endpoints.
 *   6. Unexpose round-trips back to the bare port output.
 */

import { test, expect, type APIRequestContext } from '@playwright/test';

async function seedNamespace(api: APIRequestContext, id: string) {
    const r = await api.post('/api/dag/nodes', {
        data: { id, type: 'namespace', config: { name: id }, parents: [], perms: 'rw*' },
    });
    if (!r.ok() && r.status() !== 409) {
        throw new Error(`seedNamespace ${id} failed: ${r.status()} ${await r.text()}`);
    }
}

async function seedVar(api: APIRequestContext, id: string, value: string, parent: string) {
    const name = id.split('/').pop()!;
    const r = await api.post('/api/dag/nodes', {
        data: {
            id, type: 'var',
            config: { name, value },
            parents: [parent],
            perms: 'rw-',
        },
    });
    if (!r.ok() && r.status() !== 409) {
        throw new Error(`seedVar ${id} failed: ${r.status()} ${await r.text()}`);
    }
}

async function installEcho(api: APIRequestContext) {
    const r = await api.post('/api/modules', {
        data: {
            module_id: 'echo',
            source: 'https://github.com/zeropoint-os/echo@61266f0673fa72bbf47ea0159e3351006f5a68c3',
        },
    });
    if (!r.ok() && r.status() !== 409) {
        throw new Error(`module install failed: ${r.status()} ${await r.text()}`);
    }
}

async function resolveAll(api: APIRequestContext) {
    const r = await api.post('/api/dag/resolve', { data: { mode: 'mock' } });
    if (!r.ok()) throw new Error(`resolve failed: ${r.status()}`);
}

async function deleteIfPresent(api: APIRequestContext, id: string) {
    await api.put(`/api/dag/${id}`, { data: { perms: 'rwd' } });
    await api.delete(`/api/dag/${id}`);
}

test.beforeEach(async ({ request, page }) => {
    // Best-effort cleanup of any prior echo install + endpoint nodes.
    const r = await request.get('/api/dag');
    if (r.ok()) {
        const nodes = (await r.json()).nodes as { id: string }[];
        // Delete endpoints first (they're descendants but cleaner this way).
        for (const n of nodes) {
            if (n.id.startsWith('modules/echo/endpoint_')) {
                await deleteIfPresent(request, n.id);
            }
        }
        if (nodes.some(n => n.id === 'modules/echo')) {
            await deleteIfPresent(request, 'modules/echo');
        }
    }
    // Ensure base graph is in place.
    await seedNamespace(request, 'settings');
    await seedNamespace(request, 'modules');
    await seedVar(request, 'settings/zp_arch', 'amd64', 'settings');
    await seedVar(request, 'settings/zp_gpu_vendor', '', 'settings');
    await page.goto('/');
});

test('port output gets an expose button, click creates an Endpoint', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    // The synced port output is named "port_placeholder".
    await page.goto('/#/modules/echo/port_placeholder');
    await expect(page.locator('.detail-name')).toContainText('port_placeholder');

    const exposeBtn = page.getByRole('button', { name: /^expose$/i });
    await expect(exposeBtn).toBeVisible();
    await exposeBtn.click();

    // An Endpoint node appears under modules/echo.
    await expect(async () => {
        const list = await (await request.get('/api/dag')).json();
        const endpoints = (list.nodes as { id: string }[])
            .filter(n => n.id.startsWith('modules/echo/endpoint_'));
        expect(endpoints.length).toBe(1);
    }).toPass();

    // The same page now shows "unexpose" instead of "expose".
    await page.reload();
    await expect(page.getByRole('button', { name: /^unexpose$/i })).toBeVisible();
});

test('exposed http endpoint shows an Open link', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    // Expose as http with name = "echo".
    const r = await request.post('/api/expose', {
        data: { port_var_id: 'modules/echo/port_placeholder', protocol: 'http', name: 'echo' },
    });
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    const endpointId = body.endpoint_id as string;

    await page.goto(`/#${'/' + endpointId}`);
    await expect(page.locator('.detail-name')).toContainText('endpoint_echo');

    const openLink = page.getByRole('link', { name: /^open ↗$/i });
    await expect(openLink).toBeVisible();
    await expect(openLink).toHaveAttribute('href', 'http://echo.local/');
});

test('unexpose deletes the endpoint and restores the bare port view', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    await request.post('/api/expose', {
        data: { port_var_id: 'modules/echo/port_placeholder' },
    });

    await page.goto('/#/modules/echo/port_placeholder');
    await page.getByRole('button', { name: /^unexpose$/i }).click();

    await expect(async () => {
        const list = await (await request.get('/api/dag')).json();
        const endpoints = (list.nodes as { id: string }[])
            .filter(n => n.id.startsWith('modules/echo/endpoint_'));
        expect(endpoints.length).toBe(0);
    }).toPass();

    await page.reload();
    await expect(page.getByRole('button', { name: /^expose$/i })).toBeVisible();
});
