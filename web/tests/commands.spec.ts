import { test, expect } from '@playwright/test';

test('kill is immediate and a reset does not resume', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Emergency stop', exact: true }).click();
  await expect(page.getByTestId('risk-latch')).toContainText('Halted');
  await expect(page.getByTestId('strategy-state')).toContainText('Paused');
  await expect(page.getByTestId('kill-reset')).toBeVisible();
  await expect(page.getByTestId('live-armed')).toContainText('No');

  // Exercise confirmed reset through its dialog and assert strategy stays paused afterward (§15)
  await page.getByTestId('kill-reset').click();
  await expect(page.getByRole('heading', { name: /Confirm Action: RESET_RISK_LATCH/i })).toBeVisible();
  await page.getByRole('button', { name: 'Submit Command', exact: true }).click();
  await expect(page.getByTestId('risk-latch')).toContainText('Normal');
  await expect(page.getByTestId('strategy-state')).toContainText('Paused');
  await expect(page.getByTestId('kill-reset')).not.toBeVisible();
  await expect(page.getByTestId('live-armed')).toContainText('No');
});

test('live activation requires typed confirmation', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Arm LIVE Trading Mode (Confirmed Dialog)', exact: true }).click();

  await expect(page.getByRole('heading', { name: /Confirm Action: ARM_LIVE/i })).toBeVisible();
  const submitBtn = page.getByRole('button', { name: 'Submit Command', exact: true });
  await expect(submitBtn).toBeDisabled();

  // Typing incorrect text should keep submit disabled
  const input = page.getByPlaceholder(/Type "LIVE paper-demo"/i);
  await input.fill('LIVE wrong-account');
  await expect(submitBtn).toBeDisabled();

  // Typing exact required text enables submit
  await input.fill('LIVE paper-demo');
  await expect(submitBtn).toBeEnabled();
});

test('confirmed flatten requires typed symbol', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Trading', exact: true }).click();
  await page.getByRole('button', { name: 'Flatten Positions', exact: true }).click();

  await expect(page.getByRole('heading', { name: /Confirm Action: FLATTEN_POSITION/i })).toBeVisible();
  const submitBtn = page.getByRole('button', { name: 'Submit Command', exact: true });
  await expect(submitBtn).toBeDisabled();

  // Typing incorrect text should keep submit disabled
  const input = page.getByPlaceholder(/Type "FLATTEN BTCUSDT"/i);
  await input.fill('FLATTEN ETHUSDT');
  await expect(submitBtn).toBeDisabled();

  // Typing exact required phrase enables submit
  await input.fill('FLATTEN BTCUSDT');
  await expect(submitBtn).toBeEnabled();
});
