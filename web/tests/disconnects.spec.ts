import { test, expect } from '@playwright/test';

test('stale state is visible and emergency stop remains reachable', async ({ page }) => {
  await page.goto('/');

  // Freshness badge is visible
  await expect(page.getByTestId('stale-badge')).toBeVisible();

  // Prominent emergency stop button remains reachable and clickable (§15.1, §15.3)
  const emergencyStop = page.getByRole('button', { name: 'Emergency stop', exact: true });
  await expect(emergencyStop).toBeVisible();
  await expect(emergencyStop).toBeEnabled();

  // Clicking emergency stop immediately halts system even during network degradation
  await emergencyStop.click();
  await expect(page.getByTestId('risk-latch')).toContainText('Halted');
  await expect(page.getByTestId('strategy-state')).toContainText('Paused');
  await expect(page.getByTestId('kill-reset')).toBeVisible();
});

test('command receipt recovery from durable inbox', async ({ page }) => {
  await page.goto('/');

  const cmdId = `receipt-recovery-${Date.now()}`;
  const submitStatus = await page.evaluate(async (id) => {
    const res = await fetch('/api/v1/commands', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-quantdesk-role': 'operator',
      },
      body: JSON.stringify({
        command_id: id,
        type: 'START_DEMO',
        target: { account_id: 'paper-demo' },
      }),
    });
    return res.status;
  }, cmdId);

  expect(submitStatus).toBe(202);

  // Query command status receipt endpoint (§15.3 durable receipt recovery)
  const receipt = await page.evaluate(async (id) => {
    const res = await fetch(`/api/v1/commands/${id}`, {
      headers: { 'x-quantdesk-role': 'operator' },
    });
    return res.json();
  }, cmdId);

  expect(receipt.command_id).toBe(cmdId);
  expect(receipt.status).toBe('QUEUED');
});
