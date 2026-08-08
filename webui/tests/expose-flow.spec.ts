/**
 * Expose / Unexpose flow — the xDS milestone.
 *
 * The agent runs in mock mode with ZEROPOINT_XDS_FORCE=1 so the xDS
 * server starts and the cache flushes happen, but no real Envoy
 * container is spawned.
 *
 * The new model:
 *   - Module installs → terraform outputs → discovery finds
 *     {port, protocol} bundles → Service nodes auto-appear.
 *   - User clicks Expose on a Service → Exposure node created as
 *     a child of the Service.
 *   - Next resolve cycle writes the slice to the xDS cache → push.
 *   - User clicks Unexpose → Exposure deleted → cache slice dropped.
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
            source: 'https://github.com/zeropoint-os/echo@4c8b39644e2745de0c4530b5fd35a7c6d19b1ce9',
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

async function listIds(request: APIRequestContext): Promise<string[]> {
    const r = await request.get('/api/dag');
    if (!r.ok()) return [];
    return ((await r.json()).nodes as { id: string }[]).map(n => n.id);
}

test.beforeEach(async ({ request, page }) => {
    // Best-effort cleanup of any prior echo install + exposures.
    const ids = await listIds(request);
    for (const id of ids) {
        if (id.startsWith('modules/echo/main_ports/') && id.endsWith('/exposure_echo')) {
            await deleteIfPresent(request, id);
        }
    }
    if (ids.includes('modules/echo')) {
        await deleteIfPresent(request, 'modules/echo');
    }
    await seedNamespace(request, 'settings');
    await seedNamespace(request, 'modules');
    await seedVar(request, 'settings/zp_arch', 'amd64', 'settings');
    await seedVar(request, 'settings/zp_gpu_vendor', '', 'settings');
    await page.goto('/');
});

test('service appears after install + resolve, expose creates an Exposure', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    // After resolve, the discovery pass should have created a Service
    // node from echo's main_ports.http bundle.
    const afterResolve = await listIds(request);
    const serviceId = afterResolve.find(id => id.startsWith('modules/echo/main_ports/') && !id.endsWith('/_self'));
    expect(serviceId, 'expected a Service node under modules/echo/main_ports').toBeTruthy();

    // Navigate and click expose.
    await page.goto(`/#${'/' + serviceId}`);
    const exposeBtn = page.getByRole('button', { name: /^expose$/i });
    await expect(exposeBtn).toBeVisible();
    await exposeBtn.click();

    // An Exposure node appears under the Service.
    await expect(async () => {
        const ids = await listIds(request);
        const exposures = ids.filter(id => id.startsWith(serviceId + '/exposure_'));
        expect(exposures.length).toBe(1);
    }).toPass();

    await page.reload();
    await expect(page.getByRole('button', { name: /^unexpose$/i })).toBeVisible();
});

test('exposed http service shows an Open link on its Exposure child', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    const ids = await listIds(request);
    const serviceId = ids.find(id => id.startsWith('modules/echo/main_ports/') && !id.endsWith('/_self'));
    expect(serviceId).toBeTruthy();

    const r = await request.post('/api/expose', {
        data: { service_id: serviceId, name: 'echo' },
    });
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    const exposureId = body.exposure_id as string;

    await page.goto(`/#${'/' + exposureId}`);
    // The exposure was created out-of-band via the API, after this page
    // mounted and fetched the DAG. Navigating by hash alone doesn't
    // refetch, and the background poll runs on the same 5s cadence as
    // the assertion timeout — so reload to pick it up deterministically.
    await page.reload();
    await expect(page.locator('.detail-name')).toContainText('exposure_echo');

    const openLink = page.getByRole('link', { name: /^open ↗$/i });
    await expect(openLink).toBeVisible();
    await expect(openLink).toHaveAttribute('href', 'http://echo.local/');
});

test('unexpose deletes the exposure and restores the bare Service view', async ({ page, request }) => {
    await installEcho(request);
    await resolveAll(request);

    const ids = await listIds(request);
    const serviceId = ids.find(id => id.startsWith('modules/echo/main_ports/') && !id.endsWith('/_self'));
    expect(serviceId).toBeTruthy();

    await request.post('/api/expose', { data: { service_id: serviceId } });

    await page.goto(`/#${'/' + serviceId}`);
    await page.getByRole('button', { name: /^unexpose$/i }).click();

    await expect(async () => {
        const ids = await listIds(request);
        const exposures = ids.filter(id => id.startsWith(serviceId + '/exposure_'));
        expect(exposures.length).toBe(0);
    }).toPass();

    await page.reload();
    await expect(page.getByRole('button', { name: /^expose$/i })).toBeVisible();
});
