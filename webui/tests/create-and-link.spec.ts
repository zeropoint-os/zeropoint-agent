/**
 * New-node create flow + Var linking — the core stage 4/5 happy path.
 *
 * Verifies:
 *   1. Navigate to a writable namespace, click "+ add", pick a Var.
 *   2. Fill the form (name + value), save → land on the new node.
 *   3. Edit a Var, click 🔗, pick a target → linking saved.
 *
 * Specifically guards:
 *   - The "input gets cleared by the 5s poll" regression we fixed by
 *     extracting NewNodeRouteView to a stable component.
 *   - The "Var.value uses ObjectWidget JSON-textarea" regression we
 *     fixed by mapping TypeVar -> 'string' in the schema introspector.
 *   - The "link button hidden in edit mode" regression where the
 *     inspector was getting node={undefined} in edit mode and
 *     therefore couldn't tell it was a Var.
 */

import { test, expect, type APIRequestContext } from '@playwright/test';

async function seedVar(api: APIRequestContext, id: string, value: string) {
    const parent = id.split('/').slice(0, -1).join('/');
    const name = id.split('/').pop()!;
    const r = await api.post('/api/dag/nodes', {
        data: {
            id,
            type: 'var',
            config: { name, value },
            parents: parent ? [parent] : [],
            perms: 'rwd',
        },
    });
    if (!r.ok()) {
        throw new Error(`seedVar ${id} failed: ${r.status()} ${await r.text()}`);
    }
}

async function deleteIfPresent(api: APIRequestContext, id: string) {
    // Test nodes may have been created with default perms (rw-) and
    // therefore not be deletable. Make them deletable first.
    await api.put(`/api/dag/${id}`, { data: { perms: 'rwd' } });
    await api.delete(`/api/dag/${id}`);
}

test.beforeEach(async ({ page, request }) => {
    // Best-effort cleanup of test nodes from a previous run. Server
    // reuses state across runs in dev (reuseExistingServer: true) so
    // tests must be idempotent.
    await deleteIfPresent(request, 'settings/test_var');
    await deleteIfPresent(request, 'settings/source_var');
    await deleteIfPresent(request, 'settings/target_var');
    await page.goto('/');
});

test('+add → fill form → save lands on the new node, inputs survive polling', async ({ page }) => {
    // Navigate into `settings` — Tile is a div, locate by its text.
    await page.locator('.tile', { hasText: 'settings' }).first().click();
    await expect(page).toHaveURL(/#?\/settings/);

    // Click +add tile, pick the Var type via data-type attribute.
    await page.locator('.tile-add').click();
    await page.locator('.type-picker-row[data-type="var"]').click();
    await expect(page).toHaveURL(/_new\/settings\/var/);

    // Fill name + value. The inputs must survive the 5-second polling
    // re-render that previously cleared them (NewNodeRouteView extracted
    // to a stable component) AND the value field must use the StringWidget
    // path, not the JSON-parsing ObjectWidget (TypeVar → "string" in the
    // schema introspector).
    const nameInput = page.locator('.widget-input').first();
    const valueInput = page.locator('.widget-input').nth(1);
    await nameInput.fill('test_var');
    await valueInput.fill('hello playwright');

    // Wait through one full poll cycle to catch the polling regression.
    await page.waitForTimeout(6000);
    await expect(nameInput).toHaveValue('test_var');
    await expect(valueInput).toHaveValue('hello playwright');

    await page.getByRole('button', { name: /^save$/i }).click();

    // Should land on the new node.
    await expect(page).toHaveURL(/settings\/test_var/);
});

test('edit Var → 🔗 link → pick target → unlink', async ({ page, request }) => {
    // Seed: two Vars under settings — source and target.
    await seedVar(request, 'settings/source_var', 'original value');
    await seedVar(request, 'settings/target_var', 'value-from-target');

    // Navigate to the source Var.
    await page.goto('/#/settings/source_var');
    await expect(page.locator('.detail-name')).toContainText('source_var');

    // Click edit. The link button should now appear next to the value input.
    await page.getByRole('button', { name: /^edit$/i }).click();

    // The link button is an .icon-btn next to the value input.
    const linkBtn = page.locator('.value-with-link .icon-btn').first();
    await expect(linkBtn).toBeVisible();
    await linkBtn.click();

    // The Var picker modal opens. Pick target_var.
    await expect(page.locator('.modal-title')).toContainText(/link.*to/i);
    await page.locator('.type-picker-row', { hasText: 'settings/target_var' }).click();

    // After a successful link the inspector exits edit mode so the
    // stale draft can't be re-applied via Save (which would clobber
    // the just-linked value=null back to the literal).
    await expect(page.getByRole('button', { name: /^edit$/i })).toBeVisible();

    // Confirm the API actually persisted the link AND value is null.
    const after = await request.get('/api/dag/nodes/settings/source_var');
    const body = await after.json();
    expect(body.parents).toContain('settings/target_var');
    expect(body.config.value).toBeNull();

    // Now unlink via the API to test the inverse (UI path tested above).
    const un = await request.put('/api/links/settings/source_var', {
        data: { target: null },
    });
    expect(un.ok()).toBeTruthy();
});

test('linking clears the draft so Save cannot re-apply the literal', async ({ page, request }) => {
    // This was the actual bug seen against echo: user edits greeting,
    // the draft captures value='hello from zeropoint', user clicks 🔗
    // and picks a target, then clicks Save. The Save sent the stale
    // draft via PUT /api/dag/<id> and put the literal back, leaving
    // the node both linked AND literal — Var.resolve() prefers the
    // literal, so the link was effectively ignored.

    await seedVar(request, 'settings/source_var', 'original value');
    await seedVar(request, 'settings/target_var', 'value-from-target');

    await page.goto('/#/settings/source_var');
    await page.getByRole('button', { name: /^edit$/i }).click();

    // Confirm the literal is in the draft (input shows original value).
    const valueInput = page.locator('.widget-input').nth(1);
    await expect(valueInput).toHaveValue('original value');

    // Link.
    await page.locator('.value-with-link .icon-btn').first().click();
    await page.locator('.type-picker-row', { hasText: 'settings/target_var' }).click();

    // We should now be out of edit mode (no Save button to click).
    await expect(page.getByRole('button', { name: /^save$/i })).toHaveCount(0);

    // Verify the persisted state is correct: linked, no literal.
    const body = await (await request.get('/api/dag/nodes/settings/source_var')).json();
    expect(body.parents).toContain('settings/target_var');
    expect(body.config.value).toBeNull();
});
