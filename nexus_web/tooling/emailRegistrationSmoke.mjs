// Run after npm run build. Browser contracts use mocked mail responses, never real recipients.
import { chromium, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../nexus_server/static/web/', import.meta.url));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png' };
const server = createServer(async (req, res) => {
  let file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname.replace(/^\/static\/web/, ''));
  if (!file.startsWith(path.resolve(root) + path.sep) && file !== path.resolve(root)) { res.writeHead(403).end(); return; }
  try { if (!(await stat(file)).isFile()) file = path.join(root, 'index.html'); } catch { file = path.join(root, 'index.html'); }
  try { res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }); res.end(await readFile(file)); }
  catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(4199, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  let enabled = true;
  let emailFailure = null;
  const posted = [];
  const errors = [];
  const token = 'A'.repeat(43);
  await context.route('**/api/v1/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    let data = [];
    if (pathname === '/api/v1/public/bootstrap/') data = { session_authenticated: false, authentication_mode: 'on_demand', capabilities: [], authentication: { google: { enabled: true }, github: { enabled: true }, email: { signup_enabled: enabled, password_reset_enabled: enabled, username_is_email: true } } };
    if (pathname.startsWith('/api/v1/auth/email/')) {
      posted.push({ pathname, data: route.request().postDataJSON() });
      if (emailFailure) {
        await route.fulfill({ status: 400, json: { ok: false, error: { code: 'INVALID_REQUEST', message: emailFailure } } });
        return;
      }
      data = pathname.endsWith('/verify/') || pathname.endsWith('/confirm/') ? { status: 'account_created', email: 'new@example.com' } : { status: 'email_requested' };
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, data, error: null }) });
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:4199/login');
  await page.getByRole('button', { name: 'Create account with email', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Create your Nexus Cloud account' })).toBeVisible();
  await page.getByLabel('Email (your username)', { exact: true }).fill('new@example.com');
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Send verification email', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Check your inbox');
  if (posted[0]?.data.email !== 'new@example.com') throw new Error('Email-only signup payload was not sent');
  await page.goto('http://127.0.0.1:4199/login#email-action=signup&token=' + token);
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  if (page.url().includes(token)) throw new Error('Verification token remained in the URL');
  await page.getByLabel('New password', { exact: true }).fill('Orbit-Pebble-2026!');
  await page.getByLabel('Confirm new password', { exact: true }).fill('mismatch-1234');
  await page.getByRole('button', { name: 'Verify email and create account', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Passwords do not match');
  await page.getByLabel('Confirm new password', { exact: true }).fill('Orbit-Pebble-2026!');
  await page.getByRole('button', { name: 'Verify email and create account', exact: true }).click();
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('new@example.com');
  if (posted.at(-1)?.data.token !== token) throw new Error('Verification token was not submitted');
  await page.getByRole('button', { name: 'Forgot password?', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Reset your password', exact: true })).toBeVisible();
  await page.getByLabel('Email (your username)', { exact: true }).fill('new@example.com');
  await page.getByRole('button', { name: 'Send verification email', exact: true }).click();
  await expect(page.getByRole('status')).toBeVisible();
  if (posted.at(-1)?.pathname !== '/api/v1/auth/email/password-reset/') throw new Error('Wrong recovery endpoint');
  await page.goto('http://127.0.0.1:4199/login#email-action=reset&token=' + token);
  await page.getByLabel('New password', { exact: true }).fill('Another-Pebble-2026!');
  await page.getByLabel('Confirm new password', { exact: true }).fill('Another-Pebble-2026!');
  await page.getByRole('button', { name: 'Save new password', exact: true }).click();
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('new@example.com');
  if (posted.at(-1)?.pathname !== '/api/v1/auth/email/password-reset/confirm/') throw new Error('Wrong reset confirmation endpoint');
  emailFailure = 'This link is invalid or expired. Request a new email.';
  await page.goto('http://127.0.0.1:4199/login#email-action=signup&token=' + token);
  await page.getByLabel('New password', { exact: true }).fill('Orbit-Pebble-2026!');
  await page.getByLabel('Confirm new password', { exact: true }).fill('Orbit-Pebble-2026!');
  await page.getByRole('button', { name: 'Verify email and create account', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(emailFailure);
  await expect(page.getByRole('button', { name: 'Verify email and create account', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Back to sign in', exact: true }).click();
  emailFailure = null;
  await page.getByRole('button', { name: 'Create account with email', exact: true }).click();
  await page.screenshot({ path: 'qa-artifacts/email-registration-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Email (your username)', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send verification email', exact: true })).toBeInViewport();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile signup has horizontal overflow');
  await page.screenshot({ path: 'qa-artifacts/email-registration-mobile.png', fullPage: true });
  await page.getByRole('dialog').getByRole('combobox').selectOption('zh-CN');
  await expect(page.getByRole('heading', { name: '创建 Nexus Cloud 账号', exact: true })).toBeVisible();
  await expect(page.getByLabel('邮箱（即用户名）', { exact: true })).toBeVisible();
  enabled = false;
  await page.reload();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('button', { name: '使用邮箱注册', exact: true })).toHaveCount(0);
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: signup, token URL cleanup, confirmation, reset, expired-link error, desktop/mobile, Chinese copy, unavailable-mail gate; no real email sent.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
