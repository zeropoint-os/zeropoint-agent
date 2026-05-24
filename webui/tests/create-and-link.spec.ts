/**
 * New-node create flow + Var linking — the core stage 4/5 happy path.
 *
 * Verifies:
 *   1. Navigate to a writable namespace, click "+ add", pick a Var.
 *   2. Fill the form (name + value), save → land on the new node.
 *
 * Specifically guards the "input gets cleared by the 5s poll" regression
 * we fixed by extracting NewNodeRouteView to a stable component.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
    await page.goto('/');
});

test('+add → fill form → save lands on the new node, inputs survive polling', async ({ page }) => {
    // The mock-mode bootstrap seeds `settings` and `modules` root namespaces.
    // We add a Var under settings via the UI.

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
