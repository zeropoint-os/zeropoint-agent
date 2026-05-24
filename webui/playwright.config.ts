import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the zeropoint-agent WebUI.
 *
 * Tests run against a local agent server. We spawn one per test run via
 * `webServer` so each test starts from a clean, isolated graph.
 *
 * The agent serves the static WebUI bundle from `webui/dist/`, so make
 * sure to `npm run build` before running tests (or run them through
 * `npm test`, which builds first via the script in package.json).
 */
export default defineConfig({
    testDir: './tests',
    fullyParallel: false,        // single shared server; tests touch shared graph state.
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    workers: 1,                  // serial — same reason.
    reporter: process.env.CI ? 'list' : 'list',
    timeout: 60_000,             // module install can take a while.
    use: {
        baseURL: 'http://127.0.0.1:2370',
        trace: 'on-first-retry',
        screenshot: 'only-on-failure',
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],
    webServer: {
        // Spin up the agent on a clean tmp data dir for each test run.
        // Run from the repo root so the agent finds webui/dist/. Use
        // MOCK mode so resolve() never hits the network or docker.
        // We also seed the two root namespaces here, before the server
        // starts serving — tests assume settings/ and modules/ exist.
        // Force the xDS server on in mock mode so the expose flow can be
        // exercised end-to-end (snapshot push + reconciler). No real Envoy
        // is spawned; mock mode keeps Docker out of the loop.
        command: 'bash -c "set -e; cd .. && rm -rf /tmp/zp-pw && mkdir -p /tmp/zp-pw && export ZEROPOINT_ROOT_PATH=/tmp/zp-pw ZEROPOINT_MODE=mock ZEROPOINT_XDS_FORCE=1 ZEROPOINT_XDS_PORT=18002 ZEROPOINT_AGENT_LOCAL=1; zeropoint-agent node ensure namespace settings -c name=settings --perms rw* >/dev/null; zeropoint-agent node ensure namespace modules -c name=modules --perms rw* >/dev/null; unset ZEROPOINT_AGENT_LOCAL; zeropoint-agent serve"',
        url: 'http://127.0.0.1:2370/api/health',
        timeout: 30_000,
        reuseExistingServer: !process.env.CI,
        stdout: 'pipe',
        stderr: 'pipe',
    },
});
