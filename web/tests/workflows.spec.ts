// Dummy playwright spec
import { test, expect } from '@playwright/test';

test('demo is a real trading workflow', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Start demo', exact: true }).click();
  await expect(page.getByTestId('mode-banner')).toContainText('DEMO');
  await page.getByRole('link', { name: 'Trading', exact: true }).click();
  await expect(page.getByTestId('fills-table').locator('tbody tr').first()).toBeVisible();
  await page.getByRole('button', { name: 'View trace' }).first().click();
  await expect(page.getByTestId('order-trace')).toContainText('Ledger');
});
