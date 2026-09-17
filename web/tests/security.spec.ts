import { test, expect } from '@playwright/test';

test('viewer role lacks mutation buttons and direct API returns 403', async ({ page }) => {
  // Set role to viewer before loading the application
  await page.addInitScript(() => {
    localStorage.setItem('quantdesk-role', 'viewer');
  });

  await page.goto('/');

  // On Home page: Emergency stop and Start demo should be removed
  await expect(page.getByRole('button', { name: 'Emergency stop', exact: true })).not.toBeVisible();
  await expect(page.getByRole('button', { name: 'Start demo', exact: true })).not.toBeVisible();

  // On Trading page: Flatten Positions should be removed
  await page.getByRole('link', { name: 'Trading', exact: true }).click();
  await expect(page.getByRole('button', { name: /Flatten/i })).not.toBeVisible();

  // On Risk page: Emergency Kill should be removed
  await page.getByRole('link', { name: 'Risk', exact: true }).click();
  await expect(page.getByRole('button', { name: /Emergency Kill/i })).not.toBeVisible();

  // On Settings page: Arm LIVE and Save Keys should be removed
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('button', { name: /Arm LIVE/i })).not.toBeVisible();
  await expect(page.getByRole('button', { name: /Save Keys Securely/i })).not.toBeVisible();

  // Verify backend independently rejects direct mutation calls with HTTP 403 Forbidden (§15.3, §15.4)
  const status = await page.evaluate(async () => {
    const res = await fetch('/api/v1/commands', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-quantdesk-role': 'viewer',
      },
      body: JSON.stringify({
        command_id: `viewer-exploit-${Date.now()}`,
        type: 'START_DEMO',
        target: { account_id: 'paper-demo' },
      }),
    });
    return res.status;
  });

  expect(status).toBe(403);
});
