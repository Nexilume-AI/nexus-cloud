import { test, expect } from "@playwright/test";
import { mockWorkspace } from "./fixtures";

const language = (page: import("@playwright/test").Page) => page.getByRole("combobox", { name: "语言 / Language" });

test("Chinese login switches language without losing the entered email", async ({ page, context }) => {
  await mockWorkspace(context, false);
  await page.goto("/");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "登录工作区" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await dialog.getByRole("textbox", { name: "邮箱" }).fill("draft@example.test");
  await page.screenshot({ path: "test-results/localization/login-zh.png", fullPage: true });
  await dialog.getByRole("combobox").selectOption("en-US");
  await expect(dialog.getByRole("heading", { name: "Access your workspace" })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Email" })).toHaveValue("draft@example.test");
  await page.reload();
  await expect(dialog.getByRole("heading", { name: "Access your workspace" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
});

test("settings keep drafts, localize navigation and synchronize other tabs", async ({ page, context }) => {
  await mockWorkspace(context, true);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "个人资料", exact: true })).toBeVisible();
  const displayName = page.getByRole("textbox", { name: "显示名称", exact: true });
  await expect(displayName).toHaveValue("Providers");
  await displayName.fill("My unsaved Agent draft");
  await language(page).selectOption("en-US");
  await expect(page.getByRole("textbox", { name: "Display name", exact: true })).toHaveValue("My unsaved Agent draft");
  await expect(page.getByRole("heading", { name: "Personal identity", exact: true })).toBeVisible();
  await language(page).selectOption("zh-CN");
  await expect(displayName).toHaveValue("My unsaved Agent draft");
  await page.getByRole("button", { name: "快速查找", exact: true }).click();
  await page.getByPlaceholder("搜索功能和工作流程").fill("模型提供商");
  await expect(page.getByRole("dialog").getByRole("button", { name: /模型提供商/ })).toBeVisible();
  await page.keyboard.press("Escape");
  const other = await context.newPage();
  await other.goto("/settings");
  await expect(language(other)).toHaveValue("zh-CN");
  await language(other).selectOption("en-US");
  await expect(language(page)).toHaveValue("en-US");
  await expect(page.getByRole("textbox", { name: "Display name", exact: true })).toHaveValue("My unsaved Agent draft");
  await language(page).selectOption("zh-CN");
  await page.screenshot({ path: "test-results/localization/settings-zh.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("Chinese provider filters retain their protocol values", async ({ page, context }) => {
  await mockWorkspace(context, true);
  await page.goto("/providers");
  await expect(page.getByRole("heading", { name: "模型提供商", exact: true })).toBeVisible();
  const filter = page.locator("#provider-status-filter");
  await filter.selectOption("healthy");
  await expect(filter).toHaveValue("healthy");
  await expect(filter.locator("option:checked")).toHaveText("正常");
  await language(page).selectOption("en-US");
  await expect(filter).toHaveValue("healthy");
  await expect(filter.locator("option:checked")).toHaveText("Healthy");
});

test("mobile login and language selection fit the viewport", async ({ page, context }) => {
  await mockWorkspace(context, false);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("dialog").getByRole("heading", { name: "登录工作区" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("dialog").getByRole("combobox").selectOption("en-US");
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Access your workspace" })).toBeVisible();
  await page.screenshot({ path: "test-results/localization/login-mobile.png", fullPage: true });
});

test("mobile workspace navigation keeps the language control accessible", async ({ page, context }) => {
  await mockWorkspace(context, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "个人资料", exact: true })).toBeVisible();
  await expect(language(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await expect(page.getByRole("complementary").getByRole("button", { name: "关闭导航", exact: true })).toBeFocused();
  await page.getByRole("complementary").getByRole("button", { name: "关闭导航", exact: true }).click();
  await language(page).selectOption("en-US");
  await expect(page.getByRole("heading", { name: "Personal identity", exact: true })).toBeVisible();
  await language(page).selectOption("zh-CN");
  await page.screenshot({ path: "test-results/localization/settings-mobile.png", fullPage: true });
});
