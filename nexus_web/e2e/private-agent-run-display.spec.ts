import { expect, test, type Page, type Route } from "@playwright/test";
import { privateDisplayAttachmentUrl, privateDisplayReturnTo } from "../src/lib/agentDisplayNavigation";
import { enterpriseAgentDisplayNavigation } from '../src/enterprise/agentDisplayNavigation';
import type { PrivateAgentRunDisplay } from "../src/lib/types";
import { runDraftKey, quotedReply } from "../src/lib/runDrafts";
import { chatMessageText } from "../src/lib/chatMessageText";

async function chooseComposerDevice(page: Page, action: string) {
  await page.getByRole("form", { name: "Message composer" }).getByRole("button", { name: "Devices", exact: true }).click();
  await page.getByRole("menuitem", { name: action, exact: true }).click();
}

for (const [width, height] of [[1440, 1000], [1024, 768], [390, 844]]) {
  test(`compact live workspace preserves useful chat height at ${width}px`, async ({ page }, testInfo) => {
    await mockRunOnlyDisplay(page, { files: true, images: true });
    await page.setViewportSize({ width, height });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    await expect(page.getByRole("textbox", { name: "Continue this Run" })).toBeVisible();
    const chat = (await page.getByLabel("Chat history", { exact: true }).boundingBox())!;
    const composer = (await page.getByRole("form", { name: "Message composer" }).boundingBox())!;
    await testInfo.attach("height-budget", { body: JSON.stringify({ width, height, chat, composer }), contentType: "application/json" });
    console.info(`Chat height ${width}x${height}: ${chat.height}px; composer: ${composer.height}px`);
    await page.screenshot({ path: testInfo.outputPath(`compact-live-${width}.png`) });
    expect(chat.height).toBeGreaterThanOrEqual(height - (width < 768 ? 310 : 270));
    await expect(page.getByRole("heading", { name: "Run workspace", exact: true })).toHaveCount(0);
    await expect(page.getByText("Continue the conversation", { exact: true })).toHaveCount(0);
    for (const tab of await page.getByRole("tablist", { name: "Run workspace modes" }).getByRole("tab").all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [1440, 390]) {
  test(`compact composer keeps delivery and device controls inside the toolbar at ${width}px`, async ({ page }, testInfo) => {
    const state = await mockRunOnlyDisplay(page, { files: true, images: true, advancedComposer: true });
    const display = state.displays.get("run-1")!;
    display.status = "running"; display.execution_task.status = "working";
    display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [], attachment_protocol: { queue: 1, steer: 1 } };
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const composer = page.getByRole("form", { name: "Message composer" });
    const mode = composer.getByLabel("While Agent is working");
    await expect(mode).toBeVisible();
    await expect(mode).toHaveValue("queue");
    await mode.selectOption("steer");
    await expect(mode).toHaveValue("steer");
    await expect(mode).toHaveAttribute("title", /next safe step/);
    await mode.selectOption("queue");
    const attach = composer.getByRole("button", { name: "Attach", exact: true });
    await attach.click();
    await expect(page.getByRole("menu", { name: "Add attachments" })).toBeVisible();
    await page.keyboard.press("Escape");
    await composer.getByRole("button", { name: "Devices", exact: true }).click();
    await expect(page.getByRole("menu", { name: "Attached devices" })).toBeVisible();
    await page.keyboard.press("Escape");
    const input = composer.getByRole("textbox", { name: "Follow-up message" });
    await input.fill("Continue after the current task");
    await expect(composer.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
    const bounds = (await composer.boundingBox())!;
    for (const control of [mode, attach, composer.getByRole("button", { name: "Devices", exact: true }), composer.getByRole("button", { name: "Send message", exact: true })]) {
      const box = (await control.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(bounds.x);
      expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
    }
    await page.screenshot({ path: testInfo.outputPath(`compact-working-${width}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [1440, 1024, 390]) {
  test(`composer devices menu replaces standalone device bars at ${width}px`, async ({ page }, testInfo) => {
    await mockRunOnlyDisplay(page, { mobileRequired: true });
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const composer = page.getByRole("form", { name: "Message composer" });
    const trigger = composer.getByRole("button", { name: "Devices", exact: true });
    await expect(trigger).toBeVisible();
    await expect(page.getByRole("button", { name: /^(Change Computer|Change Mobile|Allow & attach)$/ })).toHaveCount(0);
    await expect(page.getByText("Old Computer", { exact: true })).toBeHidden();
    const before = (await composer.boundingBox())!;
    await trigger.focus(); await trigger.press("ArrowDown");
    const menu = page.getByRole("menu", { name: "Attached devices" });
    await expect(menu.getByRole("menuitem", { name: "Change Computer", exact: true })).toBeFocused();
    await expect(menu.getByText(/Old Computer/)).toBeVisible();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem", { name: "Attach Mobile", exact: true })).toBeFocused();
    await page.keyboard.press("Home");
    await expect(menu.getByRole("menuitem", { name: "Change Computer", exact: true })).toBeFocused();
    const after = (await composer.boundingBox())!;
    expect(Math.abs(after.y - before.y)).toBeLessThan(2);
    expect(Math.abs(after.height - before.height)).toBeLessThan(2);
    expect((await trigger.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`composer-devices-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await trigger.click();
    await page.getByRole("textbox", { name: "Continue this Run" }).click();
    await expect(menu).toHaveCount(0);
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
}

test("composer devices distinguish optional unattached from connected and hide unrequested devices", async ({ page }) => {
  await mockRunOnlyDisplay(page, { computerNotAttached: true });
  await page.goto("/agents/agent-echo/private-display");
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  const menu = page.getByRole("menu", { name: "Attached devices" });
  await expect(menu.getByRole("menuitem", { name: "Attach Computer", exact: true })).toBeVisible();
  await expect(menu.getByText("Optional · Not attached", { exact: true })).toBeVisible();
  await expect(menu.getByText("Connected", { exact: true })).toHaveCount(0);
  await expect(menu.getByRole("menuitem", { name: /Mobile/ })).toHaveCount(0);
});

test("composer hides the device menu when neither device is requested", async ({ page }) => {
  await mockRunOnlyDisplay(page, { computerDisabled: true, computerNotAttached: true });
  await page.goto("/agents/agent-echo/private-display");
  await expect(page.getByRole("textbox", { name: "Start a new Agent Run" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Devices", exact: true })).toHaveCount(0);
});

test("Run Context hides Workspace when the Agent does not request a Computer", async ({ page }) => {
  await mockRunOnlyDisplay(page, { computerDisabled: true });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("tab", { name: "Workspace", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Mobile", exact: true })).toHaveCount(0);
});

async function mockFilesWorkbench(page: Page) {
  const state = await mockRunOnlyDisplay(page, { files: true, images: true });
  const files = [
    { file_id: "input-text", name: "notes.txt", content_type: "text/plain", size_bytes: 24, state: "ready", turn_index: 1 },
    { file_id: "input-md", name: "brief.md", content_type: "text/markdown", size_bytes: 180, state: "ready", turn_index: 2 },
    { file_id: "input-photo", name: "photo.png", content_type: "image/png", size_bytes: 100, state: "ready", turn_index: 2 },
    { file_id: "input-pdf", name: "report.pdf", content_type: "application/pdf", size_bytes: 800, state: "ready", turn_index: 2 },
    { file_id: "input-large", name: "large.txt", content_type: "text/plain", size_bytes: 9 * 1024 ** 2, state: "ready", turn_index: null },
  ];
  const outputs = [{ id: "output-text", original_file_name: "result.txt", content_type: "text/plain", size_bytes: 12, snapshot_status: "ready", turn_index: 2, scan_status: "passed", policy_status: "approved" }];
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/files/", route => envelope(route, files));
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/outputs/", route => envelope(route, outputs));
  const downloads: string[] = [];
  await page.route("**/api/v1/agent-runs/run-1/**/download/", route => {
    expect(route.request().headers()["x-nexus-agent-display-token"]).toBe("token-run-1");
    expect(route.request().method()).toBe("GET");
    downloads.push(route.request().url());
    if (route.request().url().includes("input-photo")) return route.fulfill({ contentType: "application/octet-stream", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aKuoAAAAASUVORK5CYII=", "base64") });
    if (route.request().url().includes("input-pdf")) return route.fulfill({ contentType: "application/octet-stream", body: minimalPreviewPdf() });
    return route.fulfill({ contentType: "application/octet-stream", body: route.request().url().includes("input-md") ? "# A private brief\n\n![hidden](https://untrusted.invalid/tracker.png)\n<script>window.previewExecuted=true</script>" : "Private file content 中文" });
  });
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/file-reference/", route => {
    const data = route.request().method() === "POST" ? route.request().postDataJSON() : Object.fromEntries(new URL(route.request().url()).searchParams);
    return envelope(route, { kind: "run_file", id: data.id, name: "File reference", size: 12, contentType: "text/plain", runId: "run-1" });
  });
  return { state, files, outputs, downloads };
}

test("Run Context restores the selected panel on refresh without reopening Files", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Files", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
});

test("Run Context preserves selection and marks updates without changing the reader's panel", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const workspace = page.getByRole("tab", { name: "Workspace", exact: true });
  await workspace.click();
  await page.reload();
  await expect(workspace).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Plan", exact: true }).click();
  display.computer.workspace_cwd = "updated";
  await expect(workspace).toHaveAttribute("title", "Updates available");
  await expect(page.getByRole("tab", { name: "Plan", exact: true })).toHaveAttribute("aria-selected", "true");
  await workspace.click();
  await expect(page.getByText("Workspace / updated", { exact: true })).toBeVisible();
  await expect(workspace).not.toHaveAttribute("title", "Updates available");
});

test("Run Context restores a long file's scroll after tabs, expansion and delayed reload", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.route("**/api/v1/agent-runs/run-1/files/input-text/download/", async route => {
    await new Promise(resolve => setTimeout(resolve, 350));
    await route.fulfill({ contentType: "application/octet-stream", body: Array.from({ length: 200 }, (_, i) => `Line ${i}: preserved file content`).join("\n") });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  const preview = page.getByLabel("File preview content", { exact: true });
  await page.getByRole("button", { name: "Expand Files" }).click();
  await expect(preview).toContainText("Line 199");
  await preview.evaluate(node => { node.scrollTop = 740; node.dispatchEvent(new Event("scroll")); });
  await page.getByRole("button", { name: "Preview brief.md" }).click();
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  await expect.poll(() => preview.evaluate(node => node.scrollTop)).toBe(740);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Plan", exact: true }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect.poll(() => preview.evaluate(node => node.scrollTop)).toBe(740);
  await page.getByRole("button", { name: "Expand Files" }).click();
  await expect.poll(() => preview.evaluate(node => node.scrollTop)).toBe(740);
  await page.keyboard.press("Escape");
  await expect.poll(() => preview.evaluate(node => node.scrollTop)).toBe(740);
  await page.reload();
  await expect(page.getByRole("tab", { name: "Files", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => preview.evaluate(node => node.scrollTop)).toBe(740);
});

test("Run Context keeps different Run selections isolated and restores mobile Context", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.displays.set("run-2", runDisplay("run-2", "Second Run"));
  state.runs.push(runSummary("run-2", "Second Run"));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Workspace", exact: true }).click();
  await page.goto("/agents/agent-echo/private-display?run=run-2");
  await expect(page.getByRole("tab", { name: "Plan", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("tab", { name: "Workspace", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Context", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Workspace", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Workspace", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Run Context restores the file directory independently from the preview", async ({ page }) => {
  const { files } = await mockFilesWorkbench(page);
  for (let i = 0; i < 45; i++) files.push({ ...files[0], file_id: `extra-${i}`, name: `Directory file ${i}.txt` });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  const directory = page.getByLabel("File directory", { exact: true });
  await expect(directory).toContainText("Directory file 44.txt");
  await directory.evaluate(node => { node.scrollTop = 580; node.dispatchEvent(new Event("scroll")); });
  await page.getByRole("tab", { name: "Plan", exact: true }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect.poll(() => directory.evaluate(node => node.scrollTop)).toBe(580);
  await page.reload();
  await expect.poll(() => directory.evaluate(node => node.scrollTop)).toBe(580);
});

test("Run Files preserves the PDF page across expansion, context tabs and refresh", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.route("**/api/v1/agent-runs/run-1/files/input-pdf/download/", route => route.fulfill({ contentType: "application/octet-stream", body: minimalPreviewPdf(2) }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview report.pdf" }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.getByText("Page 2 of 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Expand Files" }).click();
  await expect(page.getByRole("dialog").getByText("Page 2 of 2", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Plan", exact: true }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByText("Page 2 of 2", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByText("Page 2 of 2", { exact: true })).toBeVisible();
});

test("Run Files retains filters and selection across context tabs and refresh", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByLabel("Search files").fill("notes");
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Plan", exact: true }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByLabel("Search files")).toHaveValue("notes");
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
});

test("Run Files sends a structured reference after refresh and leaves failed selection open", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Update this file");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview result.txt" }).click();
  await page.getByRole("button", { name: "Reference in draft" }).click();
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("result.txt");
  await page.reload();
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
  const request = page.waitForRequest(req => req.method() === "POST" && req.url().includes("/run-1/resume/"));
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  expect((await request).postDataJSON().files).toEqual(["output-text"]);
});

test("Run Files refuses unavailable references without changing draft or closing expanded view", async ({ page }) => {
  await mockFilesWorkbench(page);
  await page.route(url => url.pathname.endsWith("/file-reference/"), route => envelope(route, { message: "Output access was revoked" }, 404));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Keep my input");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Expand Files" }).click();
  const dialog = page.getByRole("dialog", { name: "Files", exact: true });
  await dialog.getByRole("button", { name: "Preview result.txt" }).click();
  await dialog.getByRole("button", { name: "Reference in draft" }).click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toHaveValue("Keep my input");
});

test("Run Files uses bounded server pages and searches beyond the first 200", async ({ page }) => {
  await mockFilesWorkbench(page);
  const files = Array.from({ length: 213 }, (_, i) => ({ file_id: `paged-${i}`, name: `evidence-${i}.txt`, content_type: "text/plain", size_bytes: 1, state: "ready", turn_index: 1 }));
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/files/", route => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get("paged")).toBe("1");
    const rows = files.filter(item => item.name.includes(params.get("q") || ""));
    const offset = Number(params.get("cursor") || 0);
    return envelope(route, { items: rows.slice(offset, offset + 50), next_cursor: rows.length > offset + 50 ? String(offset + 50) : null });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByRole("button", { name: "Preview evidence-0.txt", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next inputs", exact: true }).click();
  await expect(page.getByRole("button", { name: "Preview evidence-50.txt", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview evidence-0.txt", exact: true })).toHaveCount(0);
  await page.getByLabel("Search files").fill("evidence-212.txt");
  await expect(page.getByRole("button", { name: "Preview evidence-212.txt", exact: true })).toBeVisible();
});
function minimalPreviewPdf(pages = 1) {
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", `<< /Type /Pages /Kids [${Array(pages).fill("3 0 R").join(" ")}] /Count ${pages} >>`, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"];
  const stream = "BT /F1 18 Tf 30 100 Td (Private PDF evidence) Tj ET";
  objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  let pdf = "%PDF-1.4\n"; const offsets = [0];
  objects.forEach((value, i) => { offsets.push(pdf.length); pdf += `${i + 1} 0 obj\n${value}\nendobj\n`; });
  const xref = pdf.length;
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(value => `${String(value).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return pdf;
}

function chinesePreviewPdf() {
  // A real, unembedded Adobe-GB1 CID font, as emitted by many Chinese PDF tools.
  // It requires both UniGB-UCS2-H and Adobe-GB1-UCS2 predefined CMaps.
  const content = "BT /F1 24 Tf 30 100 Td <4e2d65876d4b8bd5> Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /DW 1000 /FontDescriptor 7 0 R >>",
    "<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1006 880] /Ascent 752 /Descent -271 /CapHeight 737 /ItalicAngle 0 /StemV 58 >>",
  ];
  let pdf = "%PDF-1.4\n"; const offsets = [0];
  objects.forEach((value, i) => { offsets.push(pdf.length); pdf += `${i + 1} 0 obj\n${value}\nendobj\n`; });
  const xref = pdf.length;
  return pdf + `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(value => `${String(value).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
}

test("Run Files renders Chinese CID PDF glyphs and text with bundled CMaps", async ({ page }, testInfo) => {
  await mockFilesWorkbench(page);
  await page.route("**/api/v1/agent-runs/run-1/files/input-pdf/download/", route => route.fulfill({ contentType: "application/octet-stream", body: chinesePreviewPdf() }));
  const warnings: string[] = [];
  page.on("console", message => { if (/cmap|font.*failed|unable to load/i.test(message.text())) warnings.push(message.text()); });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview report.pdf" }).click();
  await expect(page.getByText("Rendering PDF…", { exact: true })).toHaveCount(0);
  await page.getByText("Page text", { exact: true }).click();
  await expect(page.getByText("中文测试", { exact: true })).toBeVisible();
  const canvas = page.locator('canvas[aria-label="PDF preview: report.pdf, page 1"]');
  const ink = await canvas.evaluate((element: HTMLCanvasElement) => {
    const rgba = element.getContext("2d")!.getImageData(0, 0, element.width, element.height).data;
    let count = 0; for (let i = 0; i < rgba.length; i += 4) if (rgba[i + 3] && rgba[i] < 150 && rgba[i + 1] < 150 && rgba[i + 2] < 150) count++;
    return count;
  });
  expect(ink).toBeGreaterThan(150);
  expect(warnings).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("chinese-pdf.png") });
});

for (const width of [1440, 1024, 390]) {
  test(`Run Files workbench filters, previews, references and resizes at ${width}px`, async ({ page }, testInfo) => {
    await mockFilesWorkbench(page);
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    await page.getByRole("textbox", { name: "Continue this Run" }).fill("Keep my draft");
    if (width < 1024) await page.getByRole("tab", { name: "Context", exact: true }).click();
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    const workspace = page.getByRole("region", { name: "Run files workspace" });
    await workspace.getByLabel("Turn", { exact: true }).selectOption("1");
    await expect(workspace.getByRole("button", { name: "Preview notes.txt" })).toBeVisible();
    await expect(workspace.getByRole("button", { name: "Preview result.txt" })).toHaveCount(0);
    await workspace.getByLabel("Turn", { exact: true }).selectOption("all");
    await workspace.getByLabel("File kind").selectOption("output");
    await workspace.getByRole("button", { name: "Preview result.txt" }).click();
    await expect(workspace.getByText("Private file content 中文", { exact: true })).toBeVisible();
    if (width >= 1024) {
      const handle = page.getByRole("separator", { name: "Resize Run context" });
      const before = (await workspace.boundingBox())!.width;
      await handle.focus(); await handle.press("ArrowLeft");
      await expect.poll(async () => (await workspace.boundingBox())!.width).toBeGreaterThan(before);
      if (width === 1440) {
        const bounds = (await handle.boundingBox())!;
        await page.mouse.move(bounds.x + 6, bounds.y + 100); await page.mouse.down();
        await page.mouse.move(bounds.x - 110, bounds.y + 100); await page.mouse.up();
        await expect.poll(async () => (await workspace.boundingBox())!.width).toBeGreaterThan(before + 90);
      }
    }
    await page.screenshot({ path: testInfo.outputPath(`files-workbench-${width}.png`) });
    await workspace.getByRole("button", { name: "Reference in draft" }).click();
    const input = page.getByRole("textbox", { name: "Continue this Run" });
    await expect(input).toHaveValue(/Keep my draft[\s\S]*Run output: result.txt[\s\S]*output-text · Turn 2/);
    await expect(page).toHaveURL(/run=run-1/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.reload();
    await expect(input).toHaveValue(/Keep my draft[\s\S]*output-text/);
  });
}

for (const width of [1440, 1024, 390]) {
  test(`Run Files layout selector retains the selection and offers list, preview or both at ${width}px`, async ({ page }) => {
    await mockFilesWorkbench(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    if (width < 1024) await page.getByRole("tab", { name: "Context", exact: true }).click();
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    await page.getByRole("button", { name: "Expand Files", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Files", exact: true });
    const layout = dialog.getByRole("combobox", { name: "Files layout" });
    await dialog.getByRole("button", { name: "Preview notes.txt" }).click();
    await expect(dialog.getByText("Private file content 中文", { exact: true })).toBeVisible();
    await layout.selectOption("list");
    await expect(dialog.locator('[aria-label="File directory"]')).toBeVisible();
    await expect(dialog.getByRole("region", { name: "File preview" })).toBeHidden();
    await layout.selectOption("preview");
    await expect(dialog.locator('[aria-label="File directory"]')).toBeHidden();
    await expect(dialog.getByText("Private file content 中文", { exact: true })).toBeVisible();
    await expect(dialog.getByRole("separator", { name: "Resize file directory" })).toBeHidden();
    await layout.selectOption("both");
    if (width >= 1024) {
      await expect(dialog.locator('[aria-label="File directory"]')).toBeVisible();
      await expect(dialog.getByRole("region", { name: "File preview" })).toBeVisible();
    }
    await layout.selectOption("preview");
    await page.reload();
    if (width < 1024) await page.getByRole("tab", { name: "Context", exact: true }).click();
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Files layout" })).toHaveValue("preview");
    await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Back to files" }).click();
    await expect(page.getByRole("button", { name: "Preview notes.txt" })).toBeVisible();
    await page.getByRole("button", { name: "Preview notes.txt" }).click();
    await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Back to files" }).click();
    await expect(page.getByRole("button", { name: "Preview notes.txt" })).toBeFocused();
  });

  test(`Run Files uses an adjustable side-by-side workspace and narrow back navigation at ${width}px`, async ({ page }, testInfo) => {
    await mockFilesWorkbench(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    if (width < 1024) await page.getByRole("tab", { name: "Context", exact: true }).click();
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    await page.getByRole("button", { name: "Expand Files", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Files", exact: true });
    await dialog.getByRole("button", { name: "Preview notes.txt" }).click();
    await expect(dialog.getByText("Private file content 中文", { exact: true })).toBeVisible();
    const directory = dialog.locator('[aria-label="File directory"]');
    const preview = dialog.getByRole("region", { name: "File preview", exact: true });
    if (width >= 1024) {
      await expect(directory).toBeVisible();
      const left = (await directory.boundingBox())!;
      const right = (await preview.boundingBox())!;
      expect(left.x + left.width).toBeLessThan(right.x);
      const divider = dialog.getByRole("separator", { name: "Resize file directory" });
      await divider.focus(); await divider.press("ArrowRight");
      await expect.poll(async () => (await directory.boundingBox())!.width).toBeGreaterThan(left.width);
      const bounds = (await divider.boundingBox())!;
      await page.mouse.move(bounds.x + 8, bounds.y + 50); await page.mouse.down();
      await page.mouse.move(bounds.x + 65, bounds.y + 50); await page.mouse.up();
      await expect.poll(async () => (await directory.boundingBox())!.width).toBeGreaterThan(left.width + 40);
      await dialog.getByRole("button", { name: "Preview report.pdf" }).click();
      await expect(dialog.getByText("Page 1 of 1", { exact: true })).toBeVisible();
    } else {
      await expect(directory).toBeHidden();
      const back = dialog.getByRole("button", { name: "Back to files" });
      expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await back.click();
      await expect(directory).toBeVisible();
      await dialog.getByRole("button", { name: "Preview report.pdf" }).click();
      await expect(dialog.getByText("Page 1 of 1", { exact: true })).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`files-split-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Expand Files", exact: true })).toBeFocused();
  });
}

test("Run Files previews real PDF canvas, image and safe Markdown without external requests", async ({ page }) => {
  await mockFilesWorkbench(page);
  const external: string[] = [];
  page.on("request", request => { if (request.url().includes("untrusted.invalid")) external.push(request.url()); });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview brief.md" }).click();
  await expect(page.getByRole("heading", { name: "A private brief" })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).previewExecuted)).toBeUndefined();
  expect(external).toEqual([]);
  await page.getByRole("button", { name: "Back to files" }).click();
  await page.getByRole("button", { name: "Preview photo.png" }).click();
  const image = page.getByRole("img", { name: "photo.png", exact: true });
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(1);
  await page.getByRole("button", { name: "Back to files" }).click();
  await page.getByRole("button", { name: "Preview report.pdf" }).click();
  await expect(page.getByText("Page 1 of 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Rendering PDF…", { exact: true })).toHaveCount(0);
  const canvas = page.locator('canvas[aria-label="PDF preview: report.pdf, page 1"]');
  await expect(canvas).toBeVisible();
  expect(await canvas.evaluate((element: HTMLCanvasElement) => element.width)).toBeGreaterThan(100);
  await page.getByText("Page text", { exact: true }).click();
  await expect(page.getByText("Private PDF evidence", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Expand Files", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("Page 1 of 1", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("Run Files distinguishes errors, no matches, oversized and unknown-turn files", async ({ page }) => {
  const { downloads } = await mockFilesWorkbench(page);
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/outputs/", route => envelope(route, { message: "Unavailable" }, 503));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry output files" })).toBeVisible();
  await expect(page.getByText("No files have been attached or produced for this Run.")).toHaveCount(0);
  await page.getByLabel("Turn", { exact: true }).selectOption("unknown");
  await page.getByRole("button", { name: "Preview large.txt" }).click();
  await expect(page.getByText("This file is too large to preview. Download it instead.")).toBeVisible();
  expect(downloads).toEqual([]);
  await page.getByRole("button", { name: "Back to files" }).click();
  await page.getByLabel("Search files").fill("does-not-exist");
  await expect(page.getByRole("button", { name: "Preview large.txt" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry output files" })).toBeVisible();
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/outputs/", route => envelope(route, []));
  await page.getByRole("button", { name: "Retry output files" }).click();
  await expect(page.getByText("No matching files.")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByRole("button", { name: "Preview notes.txt" })).toBeVisible();
});

test("Run Files preserves original Computer attribution and isolates invalid preview content", async ({ page }) => {
  const { state } = await mockFilesWorkbench(page);
  const display = state.displays.get("run-1")!;
  display.computer.revision = 1;
  display.computer.history = [{ revision: 0, computer_name: "Original laptop" }, { revision: 1, computer_name: "New laptop" }];
  await page.route("**/api/v1/agent-runs/run-1/files/input-photo/download/", route => route.fulfill({ contentType: "image/png", body: "<script>window.previewExecuted=true</script>" }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await expect(page.getByText(/Source: Original laptop · attachment 1/)).toBeVisible();
  await page.getByRole("button", { name: "Preview photo.png" }).click();
  await expect(page.getByText("File content does not match a supported preview format.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry preview" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).previewExecuted)).toBeUndefined();
  await page.getByRole("button", { name: "Back to files" }).click();
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  await expect(page.getByText("Private file content 中文", { exact: true })).toBeVisible();
});

test("Run Files adds references to the running follow-up draft without submitting", async ({ page }) => {
  const { state } = await mockFilesWorkbench(page);
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task!.status = "working";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [] };
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("While you work");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByRole("button", { name: "Preview notes.txt" }).click();
  await page.getByRole("button", { name: "Reference in draft" }).click();
  await expect(input).toHaveValue(/While you work[\s\S]*Input file: notes.txt/);
  expect(display.turn_index).toBe(1);
});

for (const width of [1440, 1024, 390]) {
  test(`stable composer and chat viewport across working, question and completion at ${width}px`, async ({ page }, testInfo) => {
    await page.clock.install();
    const state = await mockRunOnlyDisplay(page);
    const display = state.displays.get("run-1")!;
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const input = page.getByRole("textbox", { name: "Continue this Run" });
    await expect(input).toBeVisible();
    const inputNode = await input.elementHandle();
    const history = page.getByLabel("Chat history", { exact: true });
    const historyNode = await history.elementHandle();
    const before = (await history.boundingBox())!;
    display.status = "running";
    display.execution_task!.status = "working";
    display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [] };
    await page.clock.fastForward(16_000);
    // Exercise live polling, not a reload that conceals layout transitions.
    const follow = page.getByRole("textbox", { name: "Follow-up message", exact: true });
    await expect(follow).toBeVisible();
    expect(await inputNode!.evaluate(node => node.isConnected)).toBe(true);
    expect(await historyNode!.evaluate(node => node.isConnected)).toBe(true);
    const working = (await history.boundingBox())!;
    expect(Math.abs(working.y - before.y)).toBeLessThanOrEqual(2);
    expect(Math.abs(working.height - before.height)).toBeLessThanOrEqual(4);
    await follow.fill("Keep this queued draft");
    display.status = "input_required";
    display.execution_task!.status = "input_required";
    display.interactions = [{ id: "stable-question", key: "q", kind: "text", prompt: "Which file?", choices: [], response: {}, status: "pending", expires_at: "", answered_at: null }];
    await page.clock.fastForward(6_000);
    const reply = page.getByRole("textbox", { name: "Reply to current Run" });
    await expect(reply).toBeVisible();
    expect(await inputNode!.evaluate(node => node.isConnected)).toBe(true);
    await expect(follow).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Send message", exact: true })).toHaveCount(1);
    await expect(page.getByLabel("Chat history", { exact: true }).getByText("Which file?")).toBeVisible();
    const questioning = (await history.boundingBox())!;
    expect(Math.abs(questioning.y - working.y)).toBeLessThanOrEqual(2);
    expect(Math.abs(questioning.height - working.height)).toBeLessThanOrEqual(4);
    await page.screenshot({ path: testInfo.outputPath(`stable-question-${width}.png`) });
    display.status = "running";
    display.execution_task!.status = "working";
    display.interactions = [];
    await page.clock.fastForward(6_000);
    await expect(follow).toHaveValue("Keep this queued draft");
    display.status = "completed";
    display.execution_task!.status = "completed";
    await page.clock.fastForward(6_000);
    await expect(input).toBeVisible();
    expect(await inputNode!.evaluate(node => node.isConnected)).toBe(true);
    await expect(page).toHaveURL(/run=run-1/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("first submission preserves the workspace and pending message while display authorization loads", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  let tokenRequest: Route | undefined;
  await page.route("**/agent-runs/run-2/display-token/", route => { tokenRequest = route; });
  await page.goto("/agents/agent-echo/private-display");
  const input = page.getByRole("textbox", { name: "Start a new Agent Run" });
  await input.fill("Do not flash this first instruction");
  const inputNode = await input.elementHandle();
  const history = page.getByLabel("Chat history", { exact: true });
  const historyNode = await history.elementHandle();
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => Boolean(tokenRequest)).toBe(true);
  expect(await historyNode!.evaluate(node => node.isConnected)).toBe(true);
  expect(await inputNode!.evaluate(node => node.isConnected)).toBe(true);
  await expect(history.getByText("Do not flash this first instruction", { exact: true })).toHaveCount(1);
  await envelope(tokenRequest!, { run_id: "run-2", display_token: "token-run-2", expires_at: "2026-08-17T03:00:00Z" });
  await expect(page).toHaveURL(/run=run-2/);
  await expect(history.getByText("Workspace inspection complete.", { exact: true })).toBeVisible();
  await expect(history.getByText("Do not flash this first instruction", { exact: true })).toHaveCount(1);
});

test("display token renewal keeps the same Run content, composer and reading position", async ({ page }) => {
  await page.clock.install();
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.messages = Array.from({ length: 35 }, (_, i) => ({ ...assistantMessage, id: `renew-${i}`, sequence: i + 1, content: `History ${i}`, content_blocks: [{ type: "markdown", text: `History ${i}` }] }));
  let generation = 0;
  let pending: Route | undefined;
  await page.route("**/agent-runs/run-1/display-token/", route => envelope(route, { run_id: "run-1", display_token: `renew-${++generation}`, expires_at: "2026-08-17T03:00:00Z" }));
  await page.route("**/agent-runs/run-1/display/", route => {
    if (generation > 1) { pending = route; return; }
    return envelope(route, display);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const history = page.getByLabel("Chat history", { exact: true });
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await expect(input).toBeEnabled();
  await input.fill("Unsent draft during renewal");
  await history.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll")); });
  const historyNode = await history.elementHandle();
  const inputNode = await input.elementHandle();
  await page.clock.fastForward(31 * 60_000);
  await expect.poll(() => Boolean(pending)).toBe(true);
  expect(await historyNode!.evaluate(node => node.isConnected)).toBe(true);
  expect(await inputNode!.evaluate(node => node.isConnected)).toBe(true);
  await expect(input).toHaveValue("Unsent draft during renewal");
  expect(await history.evaluate(element => element.scrollTop)).toBe(0);
  await expect(history.getByText("History 0", { exact: true })).toBeVisible();
  await envelope(pending!, display);
  expect(await history.evaluate(element => element.scrollTop)).toBe(0);
});

test("chat draft keys isolate account, organization, project, Agent, Run and input type", () => {
  const make = (values: string[]) => runDraftKey(values[0], values[1], values[2], values[3], values[4], values[5]);
  const base = ["caller", "org", "project", "agent", "run", "chat"];
  const keys = [make(base), ...base.map((_, index) => make(base.map((value, i) => i === index ? `${value}-other` : value)))];
  expect(new Set(keys).size).toBe(7);
  expect(runDraftKey(undefined, "org", "project", "agent", "run")).toBe("");
  expect(quotedReply("Hello\nworld", "assistant", "My answer")).toBe("> Agent\n> Hello\n> world\n\nMy answer");
  const copied = chatMessageText([{ type: "file", name: "report.txt", url: "/api/v1/agent-runs/private/files/secret" }, { type: "image", alt: "Preview", url: "/api/v1/agent-runs/private/assets/secret" }], "");
  expect(copied).toBe("[File: report.txt]\n\n[Image: Preview]");
  expect(copied).not.toContain("secret");
});

test("chat drafts survive refresh and history navigation without crossing conversations", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.runs.push(runSummary("run-other", "Other conversation"));
  state.displays.set("run-other", runDisplay("run-other", "Other instruction"));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("第一段草稿🙂");
  await page.getByRole("button", { name: /Other conversation.*completed/i }).click();
  await expect(input).toHaveValue("");
  await input.fill("Second draft");
  await page.getByRole("button", { name: /^Workspace inspection complete.*completed/i }).click();
  await expect(input).toHaveValue("第一段草稿🙂");
  await page.reload();
  await expect(input).toHaveValue("第一段草稿🙂");
  await page.getByRole("button", { name: "New run", exact: true }).click();
  const fresh = page.getByRole("textbox", { name: "Start a new Agent Run" });
  await expect(fresh).toHaveValue("");
  await fresh.fill("Unsent new conversation");
  await page.getByRole("button", { name: /^Workspace inspection complete.*completed/i }).click();
  await expect(input).toHaveValue("第一段草稿🙂");
  await page.getByRole("button", { name: "New run", exact: true }).click();
  await expect(fresh).toHaveValue("Unsent new conversation");
  const saved = await page.evaluate(() => sessionStorage.getItem("nexus.private-run-drafts.v1"));
  expect(saved).not.toMatch(/display_token|private-run-token|Authorization|asset_id/);
});

test("chat draft failure retention and successful submission clear only the submitted Run", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.runs.push(runSummary("run-other", "Other conversation"));
  state.displays.set("run-other", runDisplay("run-other", "Other instruction"));
  let fail = true;
  let pending: Route | undefined;
  await page.route("**/agent-runs/run-1/resume/", async route => {
    if (fail) return envelope(route, { message: "Temporarily unavailable" }, 503);
    pending = route;
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("Keep this until confirmed");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(input).toBeEnabled();
  await expect(input).toHaveValue("Keep this until confirmed");
  await page.reload();
  await expect(input).toHaveValue("Keep this until confirmed");
  fail = false;
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole("button", { name: /Other conversation.*completed/i }).click();
  await envelope(pending!, { run: state.runs[0], run_id: "run-1", display_url: "/agents/agent-echo/private-display?run=run-1", status: "working", resumed: true }, 202);
  await expect(input).toBeEnabled();
  await input.fill("Do not clear the other draft");
  await expect(page).toHaveURL(/run=run-other/);
  await page.getByRole("button", { name: /^Workspace inspection complete.*completed/i }).click();
  await expect(input).toHaveValue("");
  await page.getByRole("button", { name: /Other conversation.*completed/i }).click();
  await expect(input).toHaveValue("Do not clear the other draft");
});

test("history selection changes composer ownership before deferred navigation commits", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.runs.push(runSummary("run-other", "Other conversation"));
  state.displays.set("run-other", runDisplay("run-other", "Other instruction"));
  await page.route("**/agent-runs/run-other/display/", async route => {
    await new Promise(resolve => setTimeout(resolve, 350));
    return envelope(route, state.displays.get("run-other"));
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toBeVisible();
  const oldComposerStillAttached = await page.getByRole("button", { name: /Other conversation.*completed/i }).evaluate(async button => {
    const previous = document.querySelector('textarea[aria-label="Continue this Run"]')!;
    (button as HTMLButtonElement).click();
    await Promise.resolve();
    return previous.isConnected;
  });
  expect(oldComposerStillAttached).toBe(false);
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("Only the selected conversation owns this draft");
  await page.reload();
  await expect(input).toHaveValue("Only the selected conversation owns this draft");
});

test("chat drafts isolate authenticated callers and tolerate unavailable browser storage", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  let caller = "caller-a";
  await page.route("**/auth/whoami/", route => envelope(route, { user_id: caller, email: "test@example.com", display_name: caller, is_superuser: false }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("Caller A private draft");
  caller = "caller-b";
  await page.reload();
  await expect(input).toHaveValue("");
  await input.fill("Caller B draft");
  caller = "caller-a";
  await page.reload();
  await expect(input).toHaveValue("Caller A private draft");
  await page.addInitScript(() => { Storage.prototype.setItem = () => { throw new DOMException("Disabled", "SecurityError"); }; });
  await page.reload();
  await input.fill("Still editable");
  await expect(input).toHaveValue("Still editable");
  await expect(page.getByText(/Draft is kept in this page only/)).toBeVisible();
});

test("chat logout clears browser drafts without touching other application storage", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Private draft to clear");
  await page.evaluate(() => sessionStorage.setItem("unrelated-preference", "keep"));
  await page.getByRole("button", { name: "Return to previous page" }).click();
  await page.getByRole("button", { name: "Open account menu" }).click();
  await page.getByRole("dialog", { name: "Account menu", exact: true }).getByRole("button", { name: "Sign out" }).click();
  expect(await page.evaluate(() => sessionStorage.getItem("nexus.private-run-drafts.v1"))).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem("unrelated-preference"))).toBe("keep");
});

test("chat copy and quote preserve Unicode and code indentation without auto-sending", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const source = 'Example 中文\n\n```python\nif True:\n    print("你好🙂")\n```';
  state.displays.get("run-1")!.messages[1].content = source;
  state.displays.get("run-1")!.messages[1].content_blocks = [{ type: "markdown", text: source }];
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (value: string) => { (window as any).__copiedChat = value; } } });
  });
  let submissions = 0;
  page.on("request", request => { if (request.method() === "POST" && request.url().includes("/resume/")) submissions++; });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const agent = page.locator('[data-message-id="message-agent"]');
  await agent.getByRole("button", { name: "Copy code", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__copiedChat)).toBe('if True:\n    print("你好🙂")\n');
  await agent.getByRole("button", { name: "Copy message", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__copiedChat)).toBe(source);
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("Please explain");
  await agent.getByRole("button", { name: "Quote reply" }).click();
  await expect(input).toHaveValue(quotedReply(source, "assistant", "Please explain"));
  await expect(input).toBeFocused();
  expect(submissions).toBe(0);
  await page.reload();
  await expect(input).toHaveValue(quotedReply(source, "assistant", "Please explain"));
});

test("assistant structured results use the Paper table treatment instead of the legacy dark block", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.displays.get("run-1")!.messages[1].content = "Browser inspection result";
  state.displays.get("run-1")!.messages[1].content_blocks = [
    {
      type: "fields",
      items: [
        { label: "Page title", value: "Nexilume workspace" },
        { label: "Elements", value: "18" },
      ],
    },
  ];

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const fields = page.locator('[data-message-id="message-agent"] [data-content-block="fields"]');
  await expect(fields).toContainText("Page title");
  await expect(fields).toContainText("Nexilume workspace");
  const firstRow = fields.locator(":scope > div").first();
  await expect(firstRow).toHaveCSS("background-color", "rgb(251, 251, 247)");
  await expect(firstRow).toHaveCSS("color", "rgb(52, 50, 45)");
});

test("chat clipboard rejection offers selectable manual copy, never false success", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("Denied"); } } });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const agent = page.locator('[data-message-id="message-agent"]');
  await agent.getByRole("button", { name: "Copy message" }).click();
  const manual = agent.getByRole("textbox", { name: "Manual copy message" });
  await expect(manual).toHaveValue("Workspace inspection complete.");
  await manual.focus();
  expect(await manual.evaluate((element: HTMLTextAreaElement) => element.selectionEnd - element.selectionStart)).toBe("Workspace inspection complete.".length);
  await expect(agent.getByRole("button", { name: "Copy message" })).toHaveText("Copy");
});

test("chat queue, steer and interaction drafts are separate and quoting respects selected mode", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Normal next turn");
  display.status = "running";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [] };
  await page.reload();
  const follow = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await expect(follow).toHaveValue("");
  await follow.fill("Queued draft");
  await page.getByLabel("While Agent is working").selectOption("steer");
  await expect(follow).toHaveValue("");
  await follow.fill("Guidance draft");
  await page.locator('[data-message-id="message-agent"]').getByRole("button", { name: "Quote reply" }).click();
  await expect(follow).toHaveValue(quotedReply("Workspace inspection complete.", "assistant", "Guidance draft"));
  await page.getByLabel("While Agent is working").selectOption("queue");
  await expect(follow).toHaveValue("Queued draft");
  display.status = "input_required";
  display.interactions = [{ id: "question", key: "q", kind: "text", prompt: "Name your file", choices: [], response: {}, status: "pending", expires_at: "", answered_at: null }];
  await page.reload();
  const answer = page.getByRole("textbox", { name: "Reply to current Run" });
  await expect(answer).toHaveValue("");
  await answer.fill("answer.txt");
  await expect(page.locator('[data-message-id="message-agent"]').getByRole("button", { name: "Quote reply" })).toBeDisabled();
  await expect(follow).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("nexus.private-run-drafts.v1"))).toContain("Queued draft");
  await page.reload();
  await expect(answer).toHaveValue("answer.txt");
  display.status = "completed";
  display.interactions = [];
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toHaveValue("Normal next turn");
});

for (const width of [1440, 390]) {
  test(`chat composer grows, caps, shrinks and preserves IME input at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    await mockRunOnlyDisplay(page);
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const input = page.getByRole("textbox", { name: "Continue this Run" });
    await expect(input).toBeEnabled();
    const height = await input.evaluate(element => element.clientHeight);
    await input.fill(Array.from({ length: 5 }, (_, i) => `line ${i}`).join("\n"));
    await expect.poll(() => input.evaluate(element => element.clientHeight)).toBeGreaterThan(height);
    await input.fill(Array.from({ length: 30 }, (_, i) => `line ${i} 中文`).join("\n"));
    expect(await input.evaluate(element => element.clientHeight)).toBeLessThanOrEqual(192);
    expect(await input.evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
    await input.fill("中文");
    await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
    await expect(input).toHaveValue("中文");
    await input.press("Shift+Enter");
    await expect(input).toHaveValue("中文\n");
    await input.fill("");
    await expect.poll(() => input.evaluate(element => element.clientHeight)).toBe(height);
    expect(await page.getByRole("button", { name: "Send message", exact: true }).evaluate(element => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`chat-tools-${width}.png`), fullPage: true });
  });
}

test("chat new-message hint preserves history position and returns to latest explicitly", async ({ page }) => {
  test.setTimeout(45000);
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.messages = Array.from({ length: 24 }, (_, i) => ({ ...assistantMessage, id: `long-${i}`, sequence: i + 1, content: `Message ${i}`, content_blocks: [{ type: "markdown", text: `Message ${i}\n\nDetails to inspect.` }] }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const history = page.getByLabel("Chat history", { exact: true });
  await expect(history.locator('[data-message-id="long-23"]')).toBeVisible();
  const historyBeforeHint = await history.boundingBox();
  await history.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll")); });
  display.messages.push({ ...assistantMessage, id: "new-message", sequence: 25, content: "New response", content_blocks: [{ type: "markdown", text: "New response" }] });
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("button", { name: /New messages \(1\)/ })).toBeVisible();
  expect(await history.boundingBox()).toEqual(historyBeforeHint);
  expect(await history.evaluate(element => element.scrollTop)).toBe(0);
  display.messages.at(-1)!.content_blocks = [{ type: "markdown", text: "New response with more streaming text" }];
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByText("New response with more streaming text", { exact: true })).toBeAttached();
  await expect(page.getByRole("button", { name: /New messages \(1\)/ })).toBeVisible();
  await page.getByRole("button", { name: /New messages/ }).click();
  await expect(page.getByRole("button", { name: /New messages/ })).toHaveCount(0);
  await expect.poll(() => history.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(48);
  await page.getByRole("tab", { name: "Shell", exact: true }).click();
  display.messages.push({ ...assistantMessage, id: "while-shell", sequence: 26, content: "While viewing Shell", content_blocks: [{ type: "markdown", text: "While viewing Shell" }] });
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await page.getByRole("button", { name: /New messages/ }).click();
  await expect(page.getByRole("tab", { name: "Chat", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("While viewing Shell", { exact: true })).toBeVisible();
});

test("chat mobile context signals unread messages without switching panels", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("Workspace inspection complete.", { exact: true }).last()).toBeVisible();
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  display.messages.push({ ...assistantMessage, id: "mobile-update", sequence: 3, content: "Ready to review", content_blocks: [{ type: "markdown", text: "Ready to review" }] });
  await expect(page.getByRole("tab", { name: /Live · 1 new/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Context", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: /Live · 1 new/ }).click();
  await expect(page.getByText("Ready to review", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Live", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Run inbox tracks background tasks and reads a completion only when opened", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  state.runs.push(runSummary("background", "Background task", "running"), runSummary("question", "Needs your answer", "input_required"));
  state.displays.set("background", runDisplay("background", "Background work", "running"));
  state.displays.set("question", runDisplay("question", "Question", "input_required"));
  let reads = 0;
  page.on("request", request => { if (request.url().includes("/background/read/")) reads++; });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("combobox", { name: "Filter Run history" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Filter runs:/ })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator("option")).toHaveText(["All runs (3)", "Running (1)", "Needs input (1)", "Completed unread (0)", "Failed / stopped (0)"]);
  await page.getByPlaceholder("Search runs").focus();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("combobox", { name: "Filter Run history" })).toBeFocused();
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="running"]')).toHaveText("Running (1)");
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="input_required"]')).toHaveText("Needs input (1)");
  const background = state.displays.get("background")!;
  background.status = "completed";
  background.completed_at = "2026-08-31T04:00:00Z";
  background.completion_unread = true;
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="completed_unread"]')).toHaveText("Completed unread (1)");
  expect(reads).toBe(0);
  await page.getByRole("combobox", { name: "Filter Run history" }).selectOption("completed_unread");
  await expect(page).toHaveURL(/history=completed_unread/);
  await expect(page.locator("[data-history-run]")).toHaveCount(1);
  await expect(page.locator('[data-history-run="background"]')).toContainText("Completed · Unread");
  expect(reads).toBe(0);
  await page.locator('[data-history-run="background"]').getByRole("button").first().click();
  await expect.poll(() => reads).toBe(1);
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="completed_unread"]')).toHaveText("Completed unread (0)");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Filter Run history" })).toHaveValue("completed_unread");
  expect(background.completion_unread).toBe(false);
  await page.getByRole("combobox", { name: "Filter Run history" }).selectOption("input_required");
  await expect(page.locator("[data-history-run]")).toHaveCount(1);
  await expect(page.locator('[data-history-run="question"]')).toContainText("Needs input");
});

test("Run inbox does not mark a completed result read while viewing older messages", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.messages = Array.from({ length: 18 }, (_, index) => ({ ...assistantMessage, id: `history-${index}`, sequence: index, content: `Part ${index}`, content_blocks: [{ type: "markdown", text: `Part ${index}` }] }));
  let reads = 0;
  page.on("request", request => { if (request.url().includes("/run-1/read/")) reads++; });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const history = page.getByLabel("Chat history", { exact: true });
  await expect(history.locator('[data-message-id="history-17"]')).toBeAttached();
  await history.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event("scroll")); });
  display.status = "completed";
  display.completed_at = "2026-08-31T05:00:00Z";
  display.completion_unread = true;
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toBeEnabled();
  await page.getByRole("button", { name: "Search and filter runs" }).click();
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="completed_unread"]')).toHaveText("Completed unread (1)");
  expect(reads).toBe(0);
  await history.evaluate(element => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event("scroll")); });
  await expect.poll(() => reads).toBe(1);
  expect(display.completion_unread).toBe(false);
});

test("Run inbox paginates and restores filter/search URL without losing the active Run", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  for (let i = 0; i < 34; i++) {
    state.runs.push(runSummary(`task-${i}`, `Pending work ${i}`, "running"));
    state.displays.set(`task-${i}`, runDisplay(`task-${i}`, "Working", "running"));
  }
  await page.goto("/agents/agent-echo/private-display?run=run-1&history=running&q=Pending");
  await expect(page.locator("[data-history-run]")).toHaveCount(30);
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="running"]')).toHaveText("Running (34)");
  await page.getByRole("button", { name: "Load more runs" }).click();
  await expect(page.locator("[data-history-run]")).toHaveCount(34);
  await expect(page.getByRole("button", { name: "Load more runs" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByPlaceholder("Search runs")).toHaveValue("Pending");
  await expect(page.getByRole("combobox", { name: "Filter Run history" })).toHaveValue("running");
  await expect(page).toHaveURL(/run=run-1/);
  await page.getByRole("combobox", { name: "Filter Run history" }).selectOption("completed_unread");
  await expect(page.getByText("No matching runs", { exact: true })).toBeVisible();
});

test("Run inbox read-sync failure preserves the badge and supports explicit retry", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  state.displays.get("run-1")!.completion_unread = true;
  let fail = true;
  await page.route("**/agent-runs/run-1/read/", route => fail ? envelope(route, null, 503) : route.fallback());
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText(/Read status could not sync/)).toBeVisible();
  await page.getByRole("button", { name: "Search and filter runs" }).click();
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="completed_unread"]')).toHaveText("Completed unread (1)");
  fail = false;
  await page.getByRole("button", { name: "Retry read status" }).click();
  await expect(page.getByRole("combobox", { name: "Filter Run history" }).locator('option[value="completed_unread"]')).toHaveText("Completed unread (0)");
  await expect(page.getByText(/Read status could not sync/)).toHaveCount(0);
});

test("Run inbox mobile task reminder keeps the current workspace and has no overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  state.runs.push(runSummary("question", "Need confirmation", "input_required"));
  state.displays.set("question", runDisplay("question", "Question", "input_required"));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const reminder = page.getByRole("tab", { name: "Runs · 1" });
  await expect(reminder).toBeVisible();
  await expect(page.getByRole("tab", { name: "Live", exact: true })).toHaveAttribute("aria-selected", "true");
  await reminder.click();
  await page.getByRole("button", { name: "Search and filter runs" }).click();
  await page.getByRole("combobox", { name: "Filter Run history" }).selectOption("input_required");
  await expect(page.locator('[data-history-run="question"]')).toContainText("Needs input");
  expect(await page.getByRole("combobox", { name: "Filter Run history" }).evaluate(element => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("run-inbox-mobile.png"), fullPage: true });
});

async function mockRunCapacity(page: Page, options: { empty?: boolean; pending?: boolean; fail?: boolean } = {}) {
  await mockRunOnlyDisplay(page);
  const state = { submissions: 0, stops: 0, blocked: true, fail: options.fail ?? false, reviewed: 0 };
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/capacity-recovery/")) {
      if (request.method() === "POST") {
        state.stops++;
        const body = request.postDataJSON();
        expect(body.confirmation).toBe(true);
        expect(body.preview_token).toBe("review-current-targets");
        expect(body.exclude_run_id).toBe("run-1");
        if (state.fail) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "UNAVAILABLE", message: "Recovery request unavailable" } }) });
        if (!options.pending) state.blocked = false;
        return envelope(route, { stopped: options.pending ? 0 : 2, pending: options.pending ? 2 : 0, failed: 0, skipped: 0,
          remaining: options.pending ? 2 : 0, capacity: { used: state.blocked ? "52" : "1", limit: "5", remaining: state.blocked ? "0" : "4", state: state.blocked ? "over_limit" : "available" } });
      }
      state.reviewed++;
      return envelope(route, { eligible_count: options.empty || !state.blocked ? 0 : 2, batch_count: options.empty || !state.blocked ? 0 : 2,
        has_more: false, preview_token: "review-current-targets",
        capacity: { used: state.blocked ? "52" : "1", limit: "5", remaining: state.blocked ? "0" : "4", state: state.blocked ? "over_limit" : "available" },
        runs: options.empty || !state.blocked ? [] : [{ id: "old-a", title: "Old background run", agent_name: "Workspace Agent", hidden: false }, { id: "hidden-b", title: "Hidden background run", agent_name: "Other Agent", hidden: true }] });
    }
    if (request.method() === "POST" && (url.pathname.endsWith("/resume/") || url.pathname.endsWith("/private-runs/"))) {
      state.submissions++;
      if (state.blocked) return route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ ok: false,
        error: { code: "PLAN_QUOTA_EXCEEDED", message: "Concurrent Runs quota exceeded: 52/5 runs." } }) });
    }
    return route.fallback();
  });
  return state;
}

for (const width of [1440, 390]) {
  test(`Run capacity recovery preserves draft and stops reviewed Runs only at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await mockRunCapacity(page);
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const input = page.getByRole("textbox", { name: "Continue this Run" });
    await input.fill("Continue after freeing capacity🙂");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText("Concurrent Run limit reached", { exact: true })).toBeVisible();
    await expect(input).toHaveValue("Continue after freeing capacity🙂");
    await page.getByRole("button", { name: "Manage active runs" }).click();
    const dialog = page.getByRole("dialog", { name: "Free Run capacity" });
    await expect(dialog.getByText(/Previously removed from history/)).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Stop other runs", exact: true })).toBeInViewport();
    expect(state.stops).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`capacity-recovery-${width}.png`), fullPage: true });
    await dialog.getByRole("button", { name: "Stop other runs", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText("2 stopped");
    await expect(page.getByText("Run capacity available", { exact: true })).toBeVisible();
    expect(state.submissions).toBe(1);
    await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
    await expect(input).toHaveValue("Continue after freeing capacity🙂");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText("Continued: Continue after freeing capacity🙂", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/run=run-1/);
    expect(state.stops).toBe(1);
  });
}

test("Run capacity recovery explains no caller-owned candidates instead of deleting history", async ({ page }) => {
  const state = await mockRunCapacity(page, { empty: true });
  await page.goto("/agents/agent-echo/private-display");
  await page.getByRole("textbox", { name: "Start a new Agent Run" }).fill("new run");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.getByRole("button", { name: "Manage active runs" }).click();
  await expect(page.getByText(/Contact the Agent publisher if the limit remains reached/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop other runs", exact: true })).toBeDisabled();
  expect(state.stops).toBe(0);
});

test("Run capacity recovery handles partial network uncertainty and pending cancellation", async ({ page }) => {
  const state = await mockRunCapacity(page, { fail: true, pending: true });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("wait for capacity");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.getByRole("button", { name: "Manage active runs" }).click();
  await page.getByRole("button", { name: "Stop other runs", exact: true }).click();
  await expect(page.getByText(/Some requests may have completed/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop other runs", exact: true })).toBeDisabled();
  state.fail = false;
  await page.getByRole("button", { name: "Review again", exact: true }).click();
  await page.getByRole("button", { name: "Stop other runs", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("0 stopped · 2 awaiting cancellation");
  await expect(page.getByText("Concurrent Run limit reached", { exact: true })).toBeVisible();
  expect(state.submissions).toBe(1);
});

test("live deployed Cloud frontend smoke without mocked APIs", async ({ page }, testInfo) => {
  test.skip(process.env.NEXUS_LIVE_CLOUD_SMOKE !== "1", "Opt-in against the formal running Cloud");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/login");
  await expect(page.getByRole("dialog", { name: "Access your workspace" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in securely", exact: true })).toBeVisible();
  const health = await page.request.get("/api/v1/health/");
  expect(health.status()).toBe(200);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("cloud-deployed.png"), fullPage: true });
});

async function envelope(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify({ ok: status < 400, data, request_id: "run-only-e2e" }),
  });
}

const userMessage = (id: string, content: string) => ({
  id,
  sequence: 1,
  role: "user",
  content,
  content_blocks: [{ type: "markdown", text: content }],
  created_at: "2026-08-17T02:00:00Z",
});

const assistantMessage = {
  id: "message-agent",
  sequence: 2,
  role: "assistant",
  content: "Workspace inspection complete.",
  content_blocks: [{ type: "markdown", text: "Workspace inspection complete." }],
  created_at: "2026-08-17T02:00:03Z",
};

function runSummary(id: string, title: string, status = "completed") {
  return {
    attention_state: undefined as string | undefined,
    completion_unread: undefined as boolean | undefined,
    id,
    agent_id: "agent-echo",
    title,
    title_source: "derived",
    tool_name: "inspect",
    status,
    preview: "Workspace inspection complete.",
    started_at: "2026-08-17T02:00:00Z",
    completed_at: status === "completed" ? "2026-08-17T02:00:03Z" : null,
    updated_at: "2026-08-17T02:00:03Z",
    display_url: `/agents/agent-echo/private-display?run=${id}`,
  };
}

function runDisplay(id: string, content: string, status = "completed") {
  return {
    completion_unread: undefined as boolean | undefined,
    follow_up: undefined as PrivateAgentRunDisplay["follow_up"],
    failure: undefined as PrivateAgentRunDisplay["failure"],
    id,
    agent_id: "agent-echo",
    agent_name: "Workspace Inspector",
    display_title: "Workspace inspection complete",
    display_title_source: "derived",
    title: "Workspace Inspector: inspect",
    tool_name: "inspect",
    turn_index: 1,
    display_mode: "task",
    tool_policy: { task: true, interactive: true, continuable: true },
    status,
    project_context: { project_id: "project-private", project_name: "Private project", instructions_markdown: "Use verified sources.", revision: 4, captured_at: "2026-08-17T02:00:00Z" },
    started_at: "2026-08-17T02:00:00Z",
    completed_at: status === "completed" ? "2026-08-17T02:00:03Z" : null,
    latest_seq: 1,
    execution_task: { status, execution_state: "finished", continuable: true, recovery: { managed: true, protocol: 1, attempt: 0, state: "finished", last_committed_operation: null }, retry_count: 0, error_code: "", result: {}, expires_at: "2026-08-17T03:00:00Z" },
    computer: { attached: true, terminal_status: "completed", viewer_mode: "read_only", workspace_cwd: ".",
      revision: 0, name: "Old Computer", online: true, platform: "windows", browser_available: true, event_cursor: 0,
      history: [] as Array<{ revision: number; computer_name: string }> },
    mobile: { attached: false, name: "", status: "unavailable", capabilities: [], commands: [] },
    interactions: [],
    messages: [userMessage(`message-${id}`, content), { ...assistantMessage, content_blocks: [...assistantMessage.content_blocks] }],
  };
}

async function mockRunOnlyDisplay(page: Page, options: {
  historyMetadata?: boolean;
  files?: boolean;
  images?: boolean;
  mobileRequired?: boolean;
  computerNotAttached?: boolean;
  computerDisabled?: boolean;
  structuredTool?: boolean;
  advancedComposer?: boolean;
  externalProjectContext?: boolean;
  multipleProjects?: boolean;
  streamEvents?: Array<Record<string, unknown>>;
  terminalEvents?: Array<{ seq: number; kind: string; command_id: string; data: string; exit_code: number | null; created_at: string }>;
} = {}) {
  const runs = [runSummary("run-1", "Workspace inspection complete")];
  const displays = new Map([["run-1", runDisplay("run-1", "请检查工作区🙂")]]);
  if (options.advancedComposer) Object.assign(displays.get("run-1")!, {
    execution: { profile_id: "fast", model: "gpt-5-mini", reasoning_effort: "low" },
    usage: {
      reported: true,
      current: { turn_index: 1, profile_id: "fast", model: "gpt-5-mini", input_tokens: 12800, output_tokens: 1200, cached_input_tokens: 800, reasoning_tokens: 300, context_window: 128000, source: "gateway" },
      totals: { input_tokens: 12800, output_tokens: 1200, cached_input_tokens: 800, reasoning_tokens: 300 },
    },
  });
  let created = 1;
  let mobileReady = !options.mobileRequired;
  let mobileGranted = false;
  let runtimeAvailable = true;
  let computerBound = false;
  let projectContextAuthorized = !options.externalProjectContext;
  const runHistoryProjects: string[] = [];
  const submissions: Array<{ content?: string; arguments?: Record<string, unknown>; execution_profile_id?: string; reasoning_effort?: string }> = [];
  const marketplaceAgent = {
    id: "agent-echo", name: "Workspace Inspector", description: "Inspect your workspace", subtitle: "Caller-owned devices",
    owner_display_name: "Publisher", overview_markdown: "Inspect your workspace", logo_url: "", profile_complete: true,
    missing_fields: [], category: "Research", tags: [], tools: ["inspect"], tool_count: 1, permissions: [],
    status: "active", visibility: "public", publication_status: "published", current_version: "v1",
    pricing: { type: "free", price: "0", currency: "USD" }, resource_config: {},
    computer_requirement: "optional", workspace_capabilities: [],
    mobile_requirement: options.mobileRequired ? "required" : "disabled",
    mobile_capabilities: options.mobileRequired ? ["mobile.observe", "mobile.screen.capture", "mobile.type_text"] : [],
    availability_status: "available", can_connect: true, runtime_deployment_status: "active", runtime_health_status: "healthy",
    success_rate_30d: 100, avg_latency_ms_30d: 100, historical_calls_30d: 1, rating: 0, review_count: 0,
  };
  const computerBinding = { id: "binding-computer", agent_id: "agent-echo", connection_id: "computer-a", connection_name: "Caller Computer", is_default: true, status: "active" };

  await page.context().addInitScript(() => {
    localStorage.setItem("nexus.console.auth", JSON.stringify({
      token: "private-run-token",
      refreshToken: "private-run-refresh",
      tenantId: "tenant-private",
      projectId: "project-private",
    }));
  });

  await page.context().route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/auth/whoami/") return envelope(route, { user_id: "caller-1", email: "caller@nexus.example", display_name: "Caller", is_superuser: false });
    if (path === "/api/v1/tenants/") return envelope(route, [{ id: "tenant-private", name: "Private workspace" }]);
    if (path === "/api/v1/projects/") return envelope(route, [
      { id: "project-private", name: "Private project" },
      ...(options.multipleProjects ? [{ id: "project-secondary", name: "Secondary project" }] : []),
    ]);
    if (path === "/api/v1/marketplace/agents/") return envelope(route, [marketplaceAgent]);
    if (path === "/api/v1/marketplace/agents/agent-echo/") return envelope(route, marketplaceAgent);
    if (path === "/api/v1/api-keys/") return envelope(route, []);
    if (path === "/api/v1/agents/capabilities/") return envelope(route, { view: false, create: false, manage: false });
    if (path === "/api/v1/agents/agent-echo/workspace-grant/") return envelope(route, { agent_id: "agent-echo", declared_scopes: [], scopes: [], status: "active" });
    if (path === "/api/v1/agents/agent-echo/computer-bindings/") {
      if (request.method() === "POST") { computerBound = true; return envelope(route, computerBinding, 201); }
      return envelope(route, computerBound ? [computerBinding] : []);
    }
    if (path === "/api/v1/workspace-connections/") return envelope(route, [{
      id: "computer-a", name: "Caller Computer", connection_type: "runtime", workspace_root: ".", status: "active",
      runtime_online: true, runtime_status: "online", last_test_status: "succeeded", metadata: {},
      availability: { available: true, code: "", message: "" },
      runtime: { online: true, status: "online", capabilities: ["workspace.v1", "terminal.v1"] },
    }]);
    if (path === "/api/v1/agents/agent-echo/interactor/") return envelope(route, {
      agent: { id: "agent-echo", name: "Workspace Inspector", status: "active", visibility: "private", publication_status: "published" },
      runtime: runtimeAvailable ? { id: "runtime-1", status: "active", kind: "docker", health_status: "healthy" } : null,
      pricing: { type: "per_call", price: "0.00", currency: "USD" },
      tools: options.structuredTool ? [{ name: "browser_session", title: "Browser session", description: "Open one URL in the caller browser.", input_schema: { type: "object", properties: { url: { type: "string", format: "uri" }, actions: { type: "array" } }, required: ["url"], additionalProperties: false }, policy: { task: true, interactive: true, continuable: false, chat: false }, display_mode: "task", availability: runtimeAvailable ? { can_invoke: true, code: "", message: "" } : { can_invoke: false, code: "RUNTIME_UNAVAILABLE", message: "The Agent is offline. Waiting for it to reconnect." } }] : [{
        name: "inspect", title: "Inspect workspace", accepts_files: Boolean(options.files || options.advancedComposer),
        input_modalities: options.advancedComposer ? ["text", "audio"] : options.images ? ["text", "image"] : ["text"],
        slash_command: options.advancedComposer ? "inspect" : undefined,
        slash_description: options.advancedComposer ? "Inspect the attached workspace" : undefined,
        execution_profiles: options.advancedComposer ? [
          { id: "balanced", label: "Balanced", model: "gpt-5", is_default: true, reasoning_efforts: ["low", "medium", "high"], default_reasoning_effort: "medium", context_window: 400000 },
          { id: "fast", label: "Fast", model: "gpt-5-mini", reasoning_efforts: ["low"], default_reasoning_effort: "low", context_window: 128000 },
        ] : [],
        description: "Inspect one workspace",
        input_schema: { type: "object", properties: { message: { type: "string" }, files: { type: "array" }, audio: { type: "array" } }, required: ["message"] },
        policy: { task: true, interactive: true, continuable: true, chat: true }, display_mode: "chat",
        availability: runtimeAvailable ? { can_invoke: true, code: "", message: "" } : { can_invoke: false, code: "RUNTIME_UNAVAILABLE", message: "The Agent is offline. Waiting for it to reconnect." },
      }],
      default_tool_name: options.structuredTool ? "browser_session" : "inspect",
      private_display: options.structuredTool
        ? { available: false, tool_name: "", code: "CHAT_TOOL_REQUIRED", message: "Private Display requires exactly one available Chat tool." }
        : { available: true, tool_name: "inspect", code: runtimeAvailable ? "" : "RUNTIME_UNAVAILABLE", message: runtimeAvailable ? "" : "The Agent is offline. Waiting for it to reconnect." },
      project_context: {
        available: true,
        authorized: projectContextAuthorized,
        requires_authorization: !projectContextAuthorized,
        external_agent: Boolean(options.externalProjectContext),
        project_id: "project-private",
        project_name: "Private project",
        instructions_markdown: "Use verified sources.",
        revision: 4,
        updated_at: "2026-08-17T01:00:00Z",
      },
      computer: { requirement: options.computerDisabled ? "disabled" : "optional", declared_scopes: [], granted_scopes: [], missing_scopes: [], attached: !options.computerNotAttached, ready: true, code: "", message: "" },
      mobile: options.mobileRequired ? {
        requirement: "required",
        declared_scopes: ["mobile.observe", "mobile.screen.capture", "mobile.type_text"],
        granted_scopes: mobileGranted ? ["mobile.observe", "mobile.screen.capture", "mobile.type_text"] : [],
        missing_scopes: mobileGranted ? [] : ["mobile.observe", "mobile.screen.capture", "mobile.type_text"],
        attached: mobileReady,
        binding_id: mobileReady ? "binding-phone-a" : "",
        device_name: mobileReady ? "Caller phone A" : "",
        device_status: mobileReady ? "online" : "",
        ready: mobileReady,
        code: mobileReady ? "" : mobileGranted ? "MOBILE_REQUIRED" : "MOBILE_PERMISSION_REQUIRED",
        message: mobileReady ? "" : mobileGranted ? "Attach your Mobile." : "Allow Mobile permissions.",
      } : { requirement: "disabled", declared_scopes: [], granted_scopes: [], missing_scopes: [], attached: false, binding_id: "", device_name: "", device_status: "", ready: true, code: "", message: "" },
    });
    if (path === "/api/v1/agents/agent-echo/project-context-grant/" && request.method() === "PUT") {
      projectContextAuthorized = true;
      return envelope(route, { available: true, authorized: true, requires_authorization: false, external_agent: true, project_id: "project-private", project_name: "Private project", instructions_markdown: "Use verified sources.", revision: 4, updated_at: "2026-08-17T01:00:00Z" });
    }
    if (path === "/api/v1/agents/agent-echo/project-context-grant/" && request.method() === "DELETE") {
      projectContextAuthorized = false;
      return envelope(route, null, 204);
    }
    if (path === "/api/v1/mobile-devices/") return envelope(route, [{
      id: "phone-a", tenant_id: "tenant-private", project_id: "project-private", name: "Caller phone A", platform: "android", device_identifier: "", token_prefix: "mob_", approval_mode: "auto", online_status: "online", lifecycle_status: "online", lifecycle_detail: "Ready", recommended_action: "open_control", paired_at: "2026-08-17T01:00:00Z", pairing_expires_at: null, capabilities: {}, metadata: {}, current_package: "com.nexus.mobile", current_activity: "MainActivity", last_observation: {}, screenshot_available: false, screenshot_captured_at: null, screenshot_expires_at: null, last_seen_at: "2026-08-17T02:00:00Z", status: "active", created_by: 1, created_at: "2026-08-17T01:00:00Z", updated_at: "2026-08-17T02:00:00Z",
    }]);
    if (path === "/api/v1/agents/agent-echo/mobile-grant/" && request.method() === "GET") return envelope(route, { agent_id: "agent-echo", declared_scopes: ["mobile.observe", "mobile.screen.capture", "mobile.type_text"], scopes: mobileGranted ? ["mobile.observe", "mobile.screen.capture", "mobile.type_text"] : [], status: mobileGranted ? "active" : "revoked" });
    if (path === "/api/v1/agents/agent-echo/mobile-grant/" && request.method() === "PUT") { mobileGranted = true; return envelope(route, { agent_id: "agent-echo", declared_scopes: ["mobile.observe", "mobile.screen.capture", "mobile.type_text"], scopes: ["mobile.observe", "mobile.screen.capture", "mobile.type_text"], status: "active" }); }
    if (path === "/api/v1/agents/agent-echo/mobile-bindings/" && request.method() === "GET") return envelope(route, mobileReady ? [{ id: "binding-phone-a", agent_id: "agent-echo", device_id: "phone-a", device_name: "Caller phone A", device_status: "online", is_default: true, status: "active", last_used_at: null, created_at: "", updated_at: "" }] : []);
    if (path === "/api/v1/agents/agent-echo/mobile-bindings/" && request.method() === "POST") { mobileReady = true; mobileGranted = true; return envelope(route, { id: "binding-phone-a", agent_id: "agent-echo", device_id: "phone-a", device_name: "Caller phone A", device_status: "online", is_default: true, status: "active", last_used_at: null, created_at: "", updated_at: "" }, 201); }
    if (path === "/api/v1/agents/agent-echo/private-runs/" && request.method() === "GET") {
      runHistoryProjects.push(request.headers()["x-nexus-project"] || "");
      const query = (url.searchParams.get("q") || "").toLowerCase();
      if (options.historyMetadata) {
        const all = runs.map(item => {
          const display = displays.get(item.id)!;
          return { ...item, status: display.status, completed_at: display.completed_at, completion_unread: display.completion_unread,
            attention_state: display.status === "completed" && display.completion_unread ? "completed_unread" : display.status };
        }).filter(item => !query || item.title.toLowerCase().includes(query));
        const states = ["all", "running", "input_required", "completed_unread", "failed"];
        const counts = Object.fromEntries(states.map(state => [state, all.filter(item => state === "all" || item.attention_state === state).length]));
        const attention = url.searchParams.get("attention") || "all";
        const filtered = all.filter(item => attention === "all" || item.attention_state === attention);
        const cursor = url.searchParams.get("cursor");
        const offset = cursor ? filtered.findIndex(item => item.id === cursor) + 1 : 0;
        const results = filtered.slice(offset, offset + 30);
        return envelope(route, { results, counts, next_cursor: filtered.length > offset + 30 ? results.at(-1)!.id : "" });
      }
      return envelope(route, { results: query ? runs.filter((item) => item.title.toLowerCase().includes(query)) : runs, next_cursor: "" });
    }
    const readMatch = path.match(/^\/api\/v1\/agent-runs\/([^/]+)\/read\/$/);
    if (readMatch && request.method() === "POST") {
      const display = displays.get(readMatch[1])!;
      expect(request.headers()["x-nexus-agent-display-token"]).toBeTruthy();
      if (display.completed_at !== request.postDataJSON().completed_at) return envelope(route, null, 409);
      display.completion_unread = false;
      return envelope(route, { run_id: display.id, completion_unread: false, completed_at: display.completed_at });
    }
    if (path === "/api/v1/agents/agent-echo/private-runs/" && request.method() === "POST") {
      const body = request.postDataJSON() as { content?: string; arguments?: Record<string, unknown>; execution_profile_id?: string; reasoning_effort?: string };
      submissions.push(body);
      created += 1;
      const id = `run-${created}`;
      const summary = runSummary(id, "inspect · Aug 17, 10:00", "running");
      runs.unshift(summary);
      displays.set(id, runDisplay(id, body.content || "Structured tool input", "running"));
      return envelope(route, { run: summary, run_id: id, display_url: summary.display_url, status: "working" }, 202);
    }
    const tokenMatch = path.match(/^\/api\/v1\/agent-runs\/([^/]+)\/display-token\/$/);
    if (tokenMatch) return envelope(route, { run_id: tokenMatch[1], display_token: `token-${tokenMatch[1]}`, expires_at: "2026-08-17T03:00:00Z" });
    const resumeMatch = path.match(/^\/api\/v1\/agent-runs\/([^/]+)\/resume\/$/);
    if (resumeMatch && request.method() === "POST") {
      const body = request.postDataJSON() as { content: string };
      const display = displays.get(resumeMatch[1]);
      if (!display) return envelope(route, null, 404);
      const sequence = display.messages.length + 1;
      display.messages.push({
        ...userMessage(`message-${resumeMatch[1]}-${sequence}`, body.content),
        sequence,
      });
      display.messages.push({
        ...assistantMessage,
        id: `message-agent-${sequence + 1}`,
        sequence: sequence + 1,
        content: `Continued: ${body.content}`,
        content_blocks: [{ type: "markdown", text: `Continued: ${body.content}` }],
      });
      display.status = "completed";
      display.completed_at = "2026-08-17T02:01:00Z";
      if (display.execution_task) {
        display.execution_task.status = "completed";
        display.execution_task.error_code = "";
        display.execution_task.result = { resumed: true };
      }
      const summary = runs.find((item) => item.id === resumeMatch[1])!;
      summary.status = "completed";
      summary.completed_at = display.completed_at;
      return envelope(route, { run: summary, run_id: resumeMatch[1], display_url: summary.display_url, status: "working", resumed: true }, 202);
    }
    const displayMatch = path.match(/^\/api\/v1\/agent-runs\/([^/]+)\/display\/$/);
    if (displayMatch && request.method() === "GET") return envelope(route, displays.get(displayMatch[1]));
    if (displayMatch && request.method() === "PATCH") {
      const title = String(request.postDataJSON()?.title || "");
      const item = runs.find((candidate) => candidate.id === displayMatch[1]);
      if (item) item.title = title;
      return envelope(route, item);
    }
    if (displayMatch && request.method() === "DELETE") {
      const index = runs.findIndex((item) => item.id === displayMatch[1]);
      if (index >= 0) runs.splice(index, 1);
      return route.fulfill({ status: 204, body: "" });
    }
    if (/\/events\/$/.test(path)) return envelope(route, [{ id: "plan", run_id: path.split("/")[4], seq: 1, type: "ACTIVITY_SNAPSHOT", activityType: "PLAN", content: { steps: [{ id: "inspect", label: "Inspect workspace", state: "completed" }] }, created_at: "2026-08-17T02:00:01Z" }, ...(options.streamEvents || [])].filter(event => Number(event.seq) > Number(url.searchParams.get("cursor") || 0)).slice(0, 100));
    if (/\/outputs\/$/.test(path)) return envelope(route, []);
    if (/\/files\/$/.test(path)) return envelope(route, []);
    if (path === "/api/v1/agent-files/" && request.method() === "GET") return envelope(route, { max_file_bytes: 1024 * 1024, chunk_bytes: 1024 * 1024, max_files: 8 });
    if (path === "/api/v1/agents/agent-echo/computer-files/") return envelope(route, { path: ".", items: [{ name: "报告.md", path: "documents/报告.md", type: "file", size_bytes: 2048, modified_at: "2026-08-17T02:00:00Z" }] });
    if (path === "/api/v1/agents/agent-echo/input-files/import/" && request.method() === "POST") return envelope(route, { file_id: "computer-file-1", name: "报告.md", content_type: "text/plain; charset=utf-8", size_bytes: 2048, received_bytes: 2048, sha256: "a".repeat(64), state: "ready", error_code: "", chunk_bytes: 1024 * 1024, artifact_id: null, source_kind: "computer", source_label: "documents/报告.md", expires_at: "2026-08-18T02:00:00Z" }, 201);
    if (/\/terminal\/$/.test(path)) {
      const remaining = (options.terminalEvents || []).filter(event => event.seq > Number(url.searchParams.get("cursor") || 0));
      const page = remaining.slice(0, 500);
      return envelope(route, { id: "terminal-1", status: "completed", viewer_mode: "read_only", latest_seq: options.terminalEvents?.at(-1)?.seq || 0, next_cursor: remaining.length > 500 ? page.at(-1)?.seq || null : null, has_more: remaining.length > 500, truncated: false, computer_name: "Caller Computer", shell: "powershell", workspace_cwd: ".", last_error: "", started_at: "2026-08-17T02:00:01Z", ended_at: "2026-08-17T02:00:03Z", events: page });
    }
    const computerMatch = path.match(/^\/api\/v1\/agent-runs\/([^/]+)\/computer\/$/);
    if (computerMatch && request.method() === "POST") {
      const display = displays.get(computerMatch[1])!;
      expect(request.postDataJSON()).toEqual({ connection_id: "computer-a", expected_revision: display.computer.revision });
      expect(request.headers()["x-nexus-agent-display-token"]).toBeTruthy();
      display.computer = { ...display.computer, name: "Caller Computer", online: true, revision: display.computer.revision + 1,
        workspace_cwd: ".", terminal_status: "not_started", event_cursor: 1,
        history: [{ revision: 0, computer_name: "Old Computer" }, { revision: 1, computer_name: "Caller Computer" }] };
      return envelope(route, display);
    }
    if (computerMatch && request.method() === "GET") {
      const display = displays.get(computerMatch[1]);
      const current = display?.computer.workspace_cwd || ".";
      const requested = url.searchParams.get("path") || current;
      return envelope(route, { cwd: current, path: requested, parent: requested === "." ? null : ".", directories: requested === "." ? [{ name: "documents", path: "documents" }] : [] });
    }
    if (computerMatch && request.method() === "PATCH") {
      const display = displays.get(computerMatch[1]);
      const selected = String(request.postDataJSON()?.path || ".");
      if (display) display.computer.workspace_cwd = selected;
      return envelope(route, { cwd: selected, path: selected, parent: selected === "." ? null : ".", directories: [] });
    }
    return envelope(route, null, 404);
  });
  return {
    displays,
    runs,
    runHistoryProjects,
    submissions,
    setRuntimeAvailable(value: boolean) { runtimeAvailable = value; },
  };
}

test("Private Display switches Project scope without carrying the selected Run", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { multipleProjects: true });
  await page.goto("/agents/agent-echo/private-display?run=run-1");

  const project = page.getByLabel("Private Display Project");
  await expect(project).toHaveValue("project-private");
  await project.selectOption("project-secondary");

  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
  await expect(project).toHaveValue("project-secondary");
  await expect.poll(() => state.runHistoryProjects.includes("project-secondary")).toBe(true);
  await expect(page.getByRole("textbox", { name: "Start a new Agent Run" })).toBeVisible();
});

test("advanced composer uses verified slash tools, Computer snapshots and execution profiles", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { advancedComposer: true });
  await page.goto("/agents/agent-echo/private-display?run=run-1");

  let composer = page.getByRole("textbox", { name: "Continue this Run" });
  await expect(page.getByText("Context usage · 10%")).toBeVisible();
  await expect(page.getByText("gpt-5-mini · low", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Voice" })).toBeVisible();
  await page.getByRole("button", { name: /Next turn execution/ }).click();
  await expect(page.getByText("Observed usage · gpt-5-mini · fast", { exact: true })).toBeVisible();
  await expect(page.getByText("Gateway verified", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Next turn execution/ }).click();

  await composer.fill("/");
  const slashCommands = page.getByRole("listbox", { name: "Slash commands" });
  await expect(slashCommands).toBeVisible();
  await expect(slashCommands.getByRole("option").filter({ hasText: "/new" })).toBeVisible();
  await expect(slashCommands.getByRole("option").filter({ hasText: "/model" })).toBeVisible();
  await expect(slashCommands.getByRole("option").filter({ hasText: "/help" })).toBeVisible();
  await expect(slashCommands.getByRole("option").filter({ hasText: "/cancel" })).toHaveCount(0);
  for (const removedCommand of ["plan", "shell", "browser", "files", "computer", "mobile"]) {
    await expect(slashCommands.getByRole("option").filter({ hasText: `/${removedCommand}` })).toHaveCount(0);
  }
  await composer.press("Escape");
  await expect(slashCommands).toBeHidden();
  await expect(composer).toHaveValue("/");
  await composer.fill("");
  await composer.fill("/");
  await composer.press("ArrowDown");
  await expect(slashCommands.getByRole("option").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(slashCommands).toBeHidden();
  await expect(composer).toBeFocused();
  await composer.fill("");
  await composer.fill("/");
  await page.getByRole("option").filter({ hasText: "/inspect" }).click();
  await expect(page).toHaveURL(/private-display$/);
  composer = page.getByRole("textbox", { name: "Start a new Agent Run" });

  await composer.fill("/m");
  await composer.press("Enter");
  await expect(page.getByLabel("Execution profile")).toBeFocused();
  await expect(page.getByText("No model or token usage has been reported by the Agent or Nexus Gateway.", { exact: true })).toBeVisible();
  await page.getByLabel("Execution profile").selectOption("fast");
  await expect(page.getByLabel("Reasoning")).toHaveValue("");
  await page.getByLabel("Reasoning").selectOption("low");

  await composer.fill("/help");
  await composer.press("Enter");
  const help = page.getByRole("dialog", { name: "Private Display commands" });
  await expect(help.getByText("/inspect", { exact: true })).toBeVisible();
  await help.getByRole("button", { name: "Close", exact: true }).click();

  await composer.fill("/unknown");
  await expect(page.getByText("No matching command. Use /help to review available commands.", { exact: true })).toBeVisible();
  await composer.press("Enter");
  await expect(page.getByText("Unknown command /unknown", { exact: true })).toBeVisible();
  await expect(composer).toHaveValue("/unknown");
  expect(state.runs).toHaveLength(1);

  await composer.fill("@报");
  await expect(page.getByRole("listbox", { name: "File mentions" })).toBeVisible();
  await page.getByRole("option", { name: /报告\.md/ }).click();
  await expect(page.getByText("computer", { exact: true })).toBeVisible();

  await composer.fill("/new");
  await composer.press("Enter");
  await expect(page.getByText("computer", { exact: true })).toHaveCount(0);

  await composer.fill("@报");
  await page.getByRole("option", { name: /报告\.md/ }).click();

  await composer.fill("@报告.md 请分析这份文件");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page).toHaveURL(/run=run-2/);
  expect(state.submissions.at(-1)).toMatchObject({ execution_profile_id: "fast", reasoning_effort: "low" });
});

test("publisher default is selectable without crashing and is submitted as an unresolved default", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { advancedComposer: true });
  await page.goto("/agents/agent-echo/private-display");

  await expect(page.getByRole("button", { name: /Publisher default.*Balanced/ })).toBeVisible();
  await page.getByRole("button", { name: /Publisher default.*Balanced/ }).click();
  const profile = page.getByLabel("Execution profile");
  await profile.selectOption("fast");
  await expect(page.getByLabel("Reasoning")).toHaveValue("");
  await profile.selectOption("");
  await expect(profile).toHaveValue("");

  await page.getByRole("textbox", { name: "Start a new Agent Run" }).fill("Use the publisher default");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page).toHaveURL(/run=run-2/);
  expect(state.submissions.at(-1)).toMatchObject({ execution_profile_id: "", reasoning_effort: "" });
});

test("agent-managed execution is informational when no selectable profiles are declared", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display");
  await expect(page.getByTitle("This Agent does not expose caller-selectable execution profiles.")).toHaveText("Managed by Agent");
  await expect(page.getByRole("button", { name: /execution/i })).toHaveCount(0);
});

test("cancel slash command is available only for a cancellable Run and reports the outcome", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { advancedComposer: true });
  const display = state.displays.get("run-1")!;
  display.status = "input_required";
  display.execution_task!.status = "input_required";
  display.interactions = [{ id: "question", key: "q", kind: "text", prompt: "Continue?", choices: [], response: {}, status: "pending", expires_at: "", answered_at: null }];
  let cancellations = 0;
  await page.route("**/api/v1/agent-runs/run-1/cancel/", async route => {
    cancellations += 1;
    if (cancellations === 1) {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ ok: false, data: null, error: { code: "CANCEL_REJECTED", message: "Cancellation could not be requested." }, request_id: "req-cancel" }) });
      return;
    }
    display.status = "cancel_requested";
    display.execution_task!.status = "cancel_requested";
    await envelope(route, state.runs[0]);
  });

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const reply = page.getByRole("textbox", { name: "Reply to current Run" });
  await reply.fill("/");
  const commands = page.getByRole("listbox", { name: "Slash commands" });
  await expect(commands.getByRole("option").filter({ hasText: "/cancel" })).toBeVisible();
  await expect(commands.getByRole("option").filter({ hasText: "/new" })).toHaveCount(0);
  await reply.fill("/cancel");
  await reply.press("Enter");
  await expect(page.getByText("Cancellation could not be requested.", { exact: true })).toBeVisible();
  expect(cancellations).toBe(1);
  await reply.fill("/cancel");
  await reply.press("Enter");
  await expect(page.getByText("Cancellation requested", { exact: true })).toBeVisible();
  expect(cancellations).toBe(2);
});

for (const width of [1024, 390]) {
  test(`advanced composer remains task-focused without horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await mockRunOnlyDisplay(page, { advancedComposer: true });
    await page.goto("/agents/agent-echo/private-display");
    await expect(page.getByRole("button", { name: "Voice" })).toBeVisible();
    await expect(page.getByText("Context usage · Not reported")).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

for (const width of [1440, 390]) {
  test(`follow-up queue retires dispatched and applied receipts but keeps history and warnings at ${width}px`, async ({ page }) => {
    await page.clock.install();
    await page.setViewportSize({ width, height: 1000 });
    const state = await mockRunOnlyDisplay(page);
    const display = state.displays.get("run-1")!;
    display.status = "running"; display.execution_task.status = "working";
    const statuses = ["pending", "received", "blocked", "rejected", "not_applied", "expired", "dispatched", "dispatched", "applied", "cancelled"] as const;
    display.follow_up = { mode: "steer_and_queue", turn_index: 14, attachment_protocol: { queue: 1, steer: 1 }, items: statuses.map((status, index) => ({
      id: `receipt-${index}`, mode: status === "received" || status === "applied" || status === "rejected" || status === "not_applied" ? "steer" : "queue",
      content: index === 6 ? "k" : index === 7 ? "gf" : `Message ${status}`, status, turn_index: 1,
      dispatched_turn: index === 6 ? 11 : index === 7 ? 14 : null, code: "",
    })) };
    display.messages.push({ ...userMessage("history-k", "k"), sequence: 3 }, { ...userMessage("history-gf", "gf"), sequence: 4 });
    const mutations: string[] = [];
    page.on("request", request => { if (request.url().includes("/follow-ups/") && request.method() !== "GET") mutations.push(request.method()); });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const queue = page.getByRole("region", { name: "Run follow-up messages", exact: true });
    const history = page.getByLabel("Chat history", { exact: true });
    await expect(queue.getByText("Message pending", { exact: true })).toBeVisible();
    for (const text of ["k", "gf", "Message applied", "Message cancelled"]) await expect(queue.getByText(text, { exact: true })).toHaveCount(0);
    for (const status of statuses.slice(0, 6)) await expect(queue.getByText(`Message ${status}`, { exact: true })).toHaveCount(1);
    for (const text of ["k", "gf"]) await expect(history.getByText(text, { exact: true })).toHaveCount(1);
    const draft = page.getByRole("textbox", { name: "Follow-up message", exact: true });
    await draft.fill("Keep this unsent draft");
    Object.assign(display.follow_up.items[0], { status: "dispatched", dispatched_turn: 15 });
    display.follow_up.items[1].status = "applied";
    display.messages.push({ ...userMessage("history-pending", "Message pending"), sequence: 5 }, { ...userMessage("history-received", "Message received"), sequence: 6 });
    await page.clock.fastForward(6_000);
    await expect(queue.getByText("Message pending", { exact: true })).toHaveCount(0);
    await expect(queue.getByText("Message received", { exact: true })).toHaveCount(0);
    await expect(history.getByText("Message pending", { exact: true })).toHaveCount(1);
    await expect(history.getByText("Message received", { exact: true })).toHaveCount(1);
    await expect(draft).toHaveValue("Keep this unsent draft");
    await page.reload();
    await expect(draft).toHaveValue("Keep this unsent draft");
    await expect(queue.getByText(/Next turn started|Applied by Agent/)).toHaveCount(0);
    await expect(history.getByText("gf", { exact: true })).toHaveCount(1);
    for (const row of display.follow_up.items) if (["blocked", "rejected", "not_applied", "expired"].includes(row.status)) row.status = "cancelled";
    await page.clock.fastForward(6_000);
    await expect(queue).toHaveText("");
    expect(mutations).toEqual([]);
    expect(display.follow_up.items).toHaveLength(10);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("resuming after a failed Steer turn never appends its orphan stream to the new turn", async ({ page }) => {
  const event = (seq: number, type: string, extra = {}) => ({ id: `e${seq}`, seq, type, created_at: "2026-08-17T02:00:01Z", ...extra });
  const state = await mockRunOnlyDisplay(page, { streamEvents: [
    event(2, "RUN_STARTED"),
    event(3, "TEXT_MESSAGE_START", { messageId: "old", role: "assistant" }),
    event(4, "TEXT_MESSAGE_CONTENT", { messageId: "old", delta: "Orphan from failed turn" }),
    event(5, "RUN_ERROR"),
    event(6, "RUN_STARTED"),
    event(7, "TEXT_MESSAGE_START", { messageId: "new", role: "assistant" }),
    event(8, "TEXT_MESSAGE_CONTENT", { messageId: "new", delta: "Current guided step" }),
  ] });
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.turn_index = 2; display.execution_task.status = "working";
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("Current guided step", { exact: true })).toBeVisible();
  await expect(page.getByText("Orphan from failed turn", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("Current guided step", { exact: true })).toBeVisible();
  await expect(page.getByText("Orphan from failed turn", { exact: true })).toHaveCount(0);
});

for (const width of [1440, 390]) test(`pending steer can edit and withdraw without changing the composer at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  const state = await mockRunOnlyDisplay(page), display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [{ id: "steer", content: "Original guidance", mode: "steer", turn_index: 1, dispatched_turn: null, status: "pending", code: "", editable: true, edit_revision: "steer-1" }] };
  const methods: string[] = [];
  await page.route("**/agent-runs/run-1/follow-ups/steer/", route => {
    methods.push(route.request().method());
    const item = display.follow_up!.items[0];
    if (route.request().method() === "PATCH") {
      expect(route.request().postDataJSON()).toEqual({ content: "Corrected guidance", expected_revision: "steer-1" });
      Object.assign(item, { content: "Corrected guidance", edit_revision: "steer-2" });
      return envelope(route, display.follow_up);
    }
    expect(route.request().method()).toBe("DELETE");
    Object.assign(item, { status: "cancelled", editable: false });return envelope(route, item);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Keep unrelated draft");
  const actions = page.getByLabel("Actions for current-turn instruction: Original guidance", { exact: true });
  await expect(actions.locator('option[value="up"]')).toHaveCount(0);
  await actions.selectOption("edit");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const notice = page.getByRole("region", { name: "Edit current-turn instruction" });
  const dialog = notice.locator("..");
  await dialog.getByRole("textbox").fill("Corrected guidance");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(notice).toBeHidden();
  await page.getByLabel("Actions for current-turn instruction: Corrected guidance", { exact: true }).selectOption("remove");
  await expect(page.getByLabel("Actions for current-turn instruction: Corrected guidance", { exact: true })).toHaveCount(0);
  await expect(input).toHaveValue("Keep unrelated draft");
  expect(methods).toEqual(["PATCH", "DELETE"]);expect(state.runs).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("steer received during editing keeps draft and offers a correction instead of withdrawal", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page), display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [{ id: "steer", content: "Original guidance", mode: "steer", turn_index: 1, dispatched_turn: null, status: "pending", code: "", editable: true, edit_revision: "steer-1" }] };
  let patches = 0;
  await page.route("**/agent-runs/run-1/follow-ups/steer/", route => {
    expect(route.request().method()).toBe("PATCH");patches++;
    Object.assign(display.follow_up!.items[0], { status: "received", editable: false, edit_revision: "steer-2" });
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "FOLLOW_UP_CONFLICT", message: "code: FOLLOW_UP_ALREADY_RECEIVED" } }) });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Existing draft");
  await page.getByLabel("Actions for current-turn instruction: Original guidance").selectOption("edit");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const notice = page.getByRole("region", { name: "Edit current-turn instruction" });
  const dialog = notice.locator("..");
  await dialog.getByRole("textbox").fill("Do not lose this correction");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(dialog.getByRole("alert")).toContainText("can no longer be edited");
  await expect(dialog.getByRole("textbox")).toHaveValue("Do not lose this correction");
  await expect(dialog.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Actions for current-turn instruction: Original guidance")).toHaveCount(0);
  await page.getByRole("button", { name: "Draft a correction" }).click();
  await expect(input).toHaveValue("Existing draft\n\nCorrection to: Original guidance");
  expect(patches).toBe(1);expect(state.runs).toHaveLength(1);
});

test("streamed reply appears before the response and Run finish", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (input, init) => {
      if (!String(input).includes("/events/stream/")) return original(input, init);
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({ start(controller) {
        const event = (seq: number, type: string, extra = {}) => controller.enqueue(encoder.encode(`event: display\ndata: ${JSON.stringify({ id: `stream-${seq}`, seq, run_id: "run-1", type, messageId: "live-text", role: "assistant", ...extra })}\n\n`));
        event(2, "TEXT_MESSAGE_START");
        event(3, "TEXT_MESSAGE_CONTENT", { delta: "Live first fragment" });
        setTimeout(() => event(4, "TEXT_MESSAGE_CONTENT", { delta: " followed by another fragment" }), 1500);
        // Remain open: the UI must render both deltas without waiting for EOF.
      } }), { headers: { "Content-Type": "text/event-stream" } });
    };
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const history = page.getByLabel("Chat history", { exact: true });
  await expect(history).toContainText("Live first fragment");
  await expect(history).toContainText("Live first fragment followed by another fragment");
  expect(display.status).toBe("running");
});

test("queued messages edit and reorder without creating a new turn or clearing the composer", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  const item = (id: string, content: string) => ({ id, content, mode: "queue" as const, turn_index: 1, status: "pending" as const, code: "", created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600_000).toISOString(), editable: true });
  display.follow_up = { mode: "queue", turn_index: 1, queue_revision: "revision-1", items: [item("one", "First queued"), item("two", "Second queued")] };
  const patches: unknown[] = [];
  await page.route("**/agent-runs/run-1/follow-ups/**", route => {
    expect(route.request().method()).toBe("PATCH");
    expect(route.request().headers()["x-nexus-agent-display-token"]).toBeTruthy();
    const body = route.request().postDataJSON(); patches.push(body);
    expect(body.expected_revision).toBe(display.follow_up!.queue_revision);
    if (body.content) display.follow_up!.items[0].content = body.content;
    if (body.ids) display.follow_up!.items = body.ids.map((id: string) => display.follow_up!.items.find(row => row.id === id)!);
    display.follow_up!.queue_revision = `revision-${patches.length + 1}`;
    return envelope(route, display.follow_up);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Separate unsent draft");
  await page.getByLabel("Actions for queued message: First queued", { exact: true }).selectOption("edit");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const notice = page.getByRole("region", { name: "Edit queued message" });
  const dialog = notice.locator("..");
  await dialog.getByRole("textbox", { name: "Queued message" }).fill("First corrected");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(notice).toBeHidden();
  await page.getByLabel("Actions for queued message: Second queued", { exact: true }).selectOption("up");
  await expect.poll(() => patches.length).toBe(2);
  expect(patches[1]).toEqual({ ids: ["two", "one"], expected_revision: "revision-2" });
  await expect(input).toHaveValue("Separate unsent draft");
  expect(state.runs).toHaveLength(1);
  await page.reload();
  const actions = page.getByRole("combobox", { name: /Actions for queued message/ });
  await expect(actions.first()).toHaveAccessibleName("Actions for queued message: Second queued");
});

test("queued editor preserves changes when the message starts before saving", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, queue_revision: "one", items: [{ id: "queued", content: "Saved message", mode: "queue", turn_index: 1, status: "pending", code: "", created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 3600_000).toISOString(), editable: true }] };
  let patches = 0;
  await page.route("**/agent-runs/run-1/follow-ups/queued/", route => {
    patches++; display.follow_up!.queue_revision = "two";
    Object.assign(display.follow_up!.items[0], { status: "dispatched", editable: false, dispatched_turn: 2 });
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "FOLLOW_UP_CONFLICT", message: "code: FOLLOW_UP_QUEUE_CHANGED" } }) });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Actions for queued message: Saved message").selectOption("edit");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const notice = page.getByRole("region", { name: "Edit queued message" });
  const dialog = notice.locator("..");
  await dialog.getByRole("textbox").fill("Keep my revised wording");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(dialog.getByRole("alert")).toContainText("can no longer be edited");
  await expect(dialog.getByRole("textbox")).toHaveValue("Keep my revised wording");
  await expect(dialog.getByRole("button", { name: "Save changes" })).toBeDisabled();
  expect(patches).toBe(1);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
});

async function seedComposerAssets(page: Page, assets: Array<{ id: string; kind: string; name: string; size: number; contentType: string }>, execution = { profileId: "", reasoningEffort: "" }) {
  const key = runDraftKey("caller-1", "tenant-private", "project-private", "agent-echo", "run-1");
  await page.addInitScript(({ key, assets, execution }) => {
    if (!sessionStorage.getItem("nexus.composer-assets.v1")) sessionStorage.setItem("nexus.composer-assets.v1", JSON.stringify({ [key]: { assets, tool: "inspect", ...execution, updated: Date.now() } }));
  }, { key, assets, execution });
}

test("saved upload Computer audio and execution settings restore without reupload and clear only after sending", async ({ page }) => {
  await mockRunOnlyDisplay(page, { advancedComposer: true });
  const assets = ["upload", "computer", "audio"].map(kind => ({ id: `${kind}-saved`, kind, name: `${kind}.txt`, size: 128, contentType: "text/plain" }));
  await seedComposerAssets(page, assets, { profileId: "fast", reasoningEffort: "low" });
  let statusReads = 0;
  await page.route("**/agent-files/*/", route => {
    expect(route.request().method()).toBe("GET"); statusReads++;
    const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
    return envelope(route, { file_id: id, name: id, state: "ready", turn_index: null });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const restored = page.getByRole("region", { name: "Restored attachments" });
  await expect(restored.getByText("Ready to send", { exact: true })).toHaveCount(3);
  await page.getByRole("button", { name: /Next turn execution/ }).click();
  await expect(page.getByLabel("Execution profile")).toHaveValue("fast");
  await expect(page.getByLabel("Reasoning", { exact: true })).toHaveValue("low");
  await page.reload();
  await expect(restored.getByText("Ready to send", { exact: true })).toHaveCount(3);
  expect(statusReads).toBeGreaterThanOrEqual(6);
  const input = page.getByRole("textbox", { name: "Continue this Run" });
  await input.fill("Use my saved attachments");
  const request = page.waitForRequest(req => req.url().includes("/run-1/resume/") && req.method() === "POST");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ files: ["upload-saved", "computer-saved"], audio: ["audio-saved"], execution_profile_id: "fast", reasoning_effort: "low" });
  await expect(restored).toHaveCount(0);
  await page.reload();
  await expect(restored).toHaveCount(0);
});

test("restored attachments block submission until access expiry and incomplete uploads are resolved", async ({ page }) => {
  await mockRunOnlyDisplay(page, { files: true, images: true });
  await seedComposerAssets(page, [
    { id: "partial", kind: "upload", name: "partial.txt", size: 128, contentType: "text/plain" },
    { id: "denied", kind: "computer", name: "denied.txt", size: 128, contentType: "text/plain" },
    { id: "expired", kind: "image", name: "expired.png", size: 128, contentType: "image/png" },
  ]);
  await page.route("**/agent-files/*/", route => route.request().url().includes("partial") ? envelope(route, { state: "uploading", turn_index: null }) : route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "NOT_FOUND", message: "File not found." } }) }));
  await page.route("**/media/assets/expired/", route => envelope(route, { status: "active", purpose: "chat_input", expires_at: "2020-01-01T00:00:00Z" }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Keep text while resolving attachments");
  const restored = page.getByRole("region", { name: "Restored attachments" });
  await expect(restored).toContainText("Upload is incomplete");
  await expect(restored).toContainText("Cannot verify access");
  await expect(restored).toContainText("Image expired");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  for (const name of ["partial.txt", "denied.txt", "expired.png"]) await page.getByLabel(`Saved attachment actions: ${name}`).selectOption("remove");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "Continue this Run" })).toHaveValue("Keep text while resolving attachments");
});

test("fresh image attachment becomes a verified draft after refresh", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  let uploads = 0;
  await page.route("**/media/assets/", route => { uploads++; return envelope(route, { id: "draft-image" }, 201); });
  await page.route("**/media/assets/draft-image/", route => envelope(route, { id: "draft-image", status: "active", purpose: "chat_input", expires_at: new Date(Date.now() + 3600_000).toISOString() }));
  await page.route("**/media/assets/draft-image/signed-url/", route => envelope(route, { url: "/api/v1/media/assets/draft-image/content/?token=short-lived" }));
  await page.route("**/media/assets/draft-image/content/**", route => route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "saved.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  await expect(page.getByRole("button", { name: "Remove image saved.png" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("Ready to send");
  await expect(page.getByRole("button", { name: "Preview saved.png" })).toBeVisible();
  expect(uploads).toBe(1);
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Describe saved image");
  const request = page.waitForRequest(req => req.url().includes("/run-1/resume/") && req.method() === "POST");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  expect((await request).postDataJSON().attachments).toEqual([{ asset_id: "draft-image" }]);
});

test("saved attachments never appear in another Project and return with their conversation", async ({ page }) => {
  await mockRunOnlyDisplay(page, { files: true, multipleProjects: true });
  await seedComposerAssets(page, [{ id: "private-file", kind: "upload", name: "caller-notes.txt", size: 4, contentType: "text/plain" }]);
  await page.route("**/agent-files/private-file/", route => envelope(route, { state: "ready", turn_index: null }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("caller-notes.txt");
  await page.getByLabel("Private Display Project").selectOption("project-secondary");
  await expect(page.getByRole("textbox", { name: "Start a new Agent Run" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Restored attachments" })).toHaveCount(0);
  await page.getByLabel("Private Display Project").selectOption("project-private");
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("caller-notes.txt");
});

test("removed execution profile requires an explicit default instead of silently changing settings", async ({ page }) => {
  await mockRunOnlyDisplay(page, { advancedComposer: true });
  await seedComposerAssets(page, [], { profileId: "removed", reasoningEffort: "high" });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run" }).fill("Use deliberate settings");
  await expect(page.getByRole("alert")).toContainText("Saved execution settings are no longer available");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Use publisher default", exact: true }).click();
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
});

test("expired normal attachments do not block text queue or steer and are not sent with them", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { images: true });
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [] };
  await seedComposerAssets(page, [{ id: "expired", kind: "image", name: "expired.png", size: 128, contentType: "image/png" }]);
  await page.route("**/media/assets/expired/", route => envelope(route, { status: "active", purpose: "chat_input", expires_at: "2020-01-01T00:00:00Z" }));
  const posts: Array<Record<string, unknown>> = [];
  await page.route("**/agent-runs/run-1/follow-ups/", route => {
    posts.push(route.request().postDataJSON());
    return envelope(route, {}, 202);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("Image expired");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  for (const mode of ["queue", "steer"]) {
    await page.getByLabel("While Agent is working").selectOption(mode);
    await input.fill(`Text only ${mode}`);
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(input).toHaveValue("");
  }
  expect(posts.map(body => body.mode)).toEqual(["queue", "steer"]);
  for (const body of posts) {
    expect(body).not.toHaveProperty("images");
    expect(body).not.toHaveProperty("files");
    expect(body).not.toHaveProperty("audio");
  }
  expect(await page.evaluate(() => sessionStorage.getItem("nexus.composer-assets.v1"))).toContain("expired.png");
  expect(state.runs).toHaveLength(1);
});

test("fresh file upload survives refresh and incomplete transfer is not replayed", async ({ page }) => {
  await mockRunOnlyDisplay(page, { files: true });
  let creates = 0;
  let writes = 0;
  const file = { file_id: "fresh-file", name: "notes.txt", size_bytes: 4, content_type: "text/plain", received_bytes: 0, chunk_bytes: 1024, state: "uploading", source_kind: "upload", source_label: "", error_code: "", turn_index: null };
  await page.route("**/api/v1/agent-files/**", route => {
    const req = route.request();
    if (req.method() === "GET" && new URL(req.url()).pathname === "/api/v1/agent-files/") return envelope(route, { max_file_bytes: 1024, chunk_bytes: 1024, max_files: 8 });
    if (req.method() === "POST" && new URL(req.url()).pathname === "/api/v1/agent-files/") { creates++; return envelope(route, file, 201); }
    if (req.method() === "PUT") { writes++; file.received_bytes = 4; return envelope(route, file); }
    if (req.method() === "POST") file.state = "ready";
    return envelope(route, file);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("test") });
  await expect(page.getByRole("region", { name: "Private file attachments" })).toContainText("Ready to send");
  await page.reload();
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("Ready to send");
  expect(creates).toBe(1); expect(writes).toBe(1);
  file.state = "uploading";
  await page.reload();
  await expect(page.getByRole("region", { name: "Restored attachments" })).toContainText("Upload is incomplete");
  expect(creates).toBe(1); expect(writes).toBe(1);
});

test("follow-up length matches the server Unicode limit without truncating the draft", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  await page.route("**/agent-runs/run-1/follow-ups/", route => { posts++; return envelope(route, {}, 202); });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  const send = page.getByRole("button", { name: "Send message", exact: true });
  await input.fill("🙂".repeat(8001));
  await expect(send).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("8,000");
  await input.press("Enter");
  expect(posts).toBe(0);
  await expect(input).toHaveValue("🙂".repeat(8001));
  await page.reload();
  await expect(input).toHaveValue("🙂".repeat(8001));
  await input.fill("  " + "🙂".repeat(8000) + "  ");
  await expect(send).toBeEnabled();
  await send.click();
  await expect.poll(() => posts).toBe(1);
  await expect(input).toHaveValue("");
});

test("follow-up stays editable while sending and preserves a newer draft on confirmation", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let pending: Route | undefined;
  await page.route("**/agent-runs/run-1/follow-ups/", route => { pending = route; });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  const send = page.getByRole("button", { name: "Send message", exact: true });
  await input.fill("First instruction");
  await send.click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await expect(input).toBeEnabled();
  await input.fill("My next draft");
  await expect(send).toBeDisabled();
  await envelope(pending!, {}, 202);
  await expect(input).toHaveValue("My next draft");
  await expect(send).toBeEnabled();
  await page.reload();
  await expect(input).toHaveValue("My next draft");
});

test("validation rejection keeps an editable draft without an unresolved delivery envelope", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  await page.route("**/agent-runs/run-1/follow-ups/", async route => {
    if (++posts > 1) return envelope(route, {}, 202);
    return route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ ok: false,
      error: { code: "VALIDATION_ERROR", message: "Content is not accepted." } }) });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Rejected draft");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Content is not accepted.");
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
  await expect(input).toHaveValue("Rejected draft");
  await input.fill("Corrected draft");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => posts).toBe(2);
  await expect(input).toHaveValue("");
});

test("lost follow-up response is reconciled automatically without another POST", async ({ page }) => {
  await page.clock.install();
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  let checks = 0;
  let key = "";
  await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
    if (route.request().method() === "GET") {
      checks++;
      expect(new URL(route.request().url()).searchParams.get("idempotency_key")).toBe(key);
      return envelope(route, { submission: display.follow_up!.items[0] });
    }
    posts++;
    const body = route.request().postDataJSON();
    key = body.idempotency_key;
    display.follow_up!.items = [{ id: "reconciled", mode: "queue", turn_index: 1, dispatched_turn: null, content: body.content, status: "pending", code: "" }];
    return envelope(route, null, 503);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Only send this once");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => checks).toBe(1);
  await expect(input).toHaveValue("");
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
  await page.clock.fastForward(30_000);
  expect(checks).toBe(1);
  expect(posts).toBe(1);
});

for (const rejection of [
  { status: 401, code: "UNAUTHENTICATED", message: "Authentication expired." },
  { status: 409, code: "FOLLOW_UP_CONFLICT", message: "code: IDEMPOTENCY_CONFLICT" },
]) {
  test(`ambiguous ${rejection.status} preserves the original envelope and requires explicit retry`, async ({ page }) => {
    const state = await mockRunOnlyDisplay(page);
    const display = state.displays.get("run-1")!;
    display.status = "running";
    display.execution_task.status = "working";
    display.follow_up = { mode: "queue", turn_index: 1, items: [] };
    const submissions: unknown[] = [];
    await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
      if (route.request().method() === "GET") return envelope(route, { submission: null });
      submissions.push(route.request().postDataJSON());
      return route.fulfill({ status: rejection.status, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: rejection.code, message: rejection.message } }) });
    });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
    await input.fill("Original envelope");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    const recovery = page.getByRole("region", { name: "Message delivery" });
    await expect(recovery).toContainText("Delivery not confirmed");
    await input.fill("My newer draft");
    await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
    await input.press("Enter");
    expect(submissions).toHaveLength(1);
    await recovery.getByRole("button", { name: "Retry original message", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(2);
    expect(submissions[1]).toEqual(submissions[0]);
    await expect(input).toHaveValue("My newer draft");
  });
}

test("leaving a Run cancels delivery lookup without clearing another Run draft", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  state.runs.push(runSummary("run-other", "Other conversation"));
  state.displays.set("run-other", runDisplay("run-other", "Other instruction"));
  let waiting: Route | undefined;
  let checks = 0;
  let aborted = 0;
  page.on("requestfailed", request => { if (request.url().includes("/follow-ups/?")) aborted++; });
  await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
    if (route.request().method() === "GET") { waiting = route; checks++; return; }
    return envelope(route, null, 503);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Run one original");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => Boolean(waiting)).toBe(true);
  await page.getByRole("button", { name: /Other conversation.*completed/i }).click();
  const input = page.getByRole("textbox", { name: "Continue this Run", exact: true });
  await input.fill("Other Run private draft");
  await expect.poll(() => aborted).toBe(1);
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
  expect(checks).toBe(1);
  await page.reload();
  await expect(input).toHaveValue("Other Run private draft");
});

test("new Run creation carries a draft written while the first message is sending", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  let pending: Route | undefined;
  await page.route("**/agents/agent-echo/private-runs/", route => {
    if (route.request().method() === "POST") { pending = route; return; }
    return route.fallback();
  });
  await page.goto("/agents/agent-echo/private-display");
  const input = page.getByRole("textbox", { name: "Start a new Agent Run" });
  await input.fill("Start this task");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await input.fill("Keep my next instruction");
  state.runs.push(runSummary("run-2", "New conversation"));
  state.displays.set("run-2", runDisplay("run-2", "Start this task"));
  await envelope(pending!, { run: state.runs[1], run_id: "run-2", display_url: "/agents/agent-echo/private-display?run=run-2" }, 202);
  await expect(page).toHaveURL(/run=run-2/);
  const continued = page.getByRole("textbox", { name: "Continue this Run", exact: true });
  await expect(continued).toHaveValue("Keep my next instruction");
  await page.reload();
  await expect(continued).toHaveValue("Keep my next instruction");
});

test("offline Computer blocks sending but not drafting or refresh recovery", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.computer.online = false;
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Continue this Run", exact: true });
  const send = page.getByRole("button", { name: "Send message", exact: true });
  await input.fill("Continue when the Computer is back");
  await expect(send).toBeDisabled();
  await input.press("Enter");
  expect(state.submissions).toHaveLength(0);
  await page.reload();
  await expect(input).toHaveValue("Continue when the Computer is back");
  await expect(send).toBeDisabled();
});

test("delivery checks time out and stop without sending or restarting when the draft changes", async ({ page }) => {
  await page.clock.install();
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  let checks = 0;
  await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
    if (route.request().method() === "GET") { checks++; return; }
    posts++;
    return envelope(route, null, 503);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Do not replay");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => checks).toBe(1);
  await input.fill("Newer text during checks");
  for (const count of [2, 3]) {
    await page.clock.fastForward(5100);
    // Let the aborted fetch reject before advancing the backoff timer.
    await page.waitForTimeout(50);
    await page.clock.fastForward(3100);
    await expect.poll(() => checks).toBe(count);
  }
  await page.clock.fastForward(5100);
  const recovery = page.getByRole("region", { name: "Message delivery" });
  await expect(recovery).toContainText("Could not check delivery.");
  await input.fill("Still a draft");
  await page.clock.fastForward(30_000);
  expect(checks).toBe(3);
  expect(posts).toBe(1);
  await expect(input).toHaveValue("Still a draft");
  await expect(recovery.getByRole("button", { name: "Check status", exact: true })).toBeEnabled();
});

test("running chat queues or steers without starting another Run and restores delivery state", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [] };
  let submitted = 0;
  let cancellations = 0;
  await page.route("**/api/v1/agent-runs/run-1/cancel/", async route => {
    cancellations += 1;
    await envelope(route, state.runs[0]);
  });
  await page.route("**/agent-runs/run-1/follow-ups/", async route => {
    const body = route.request().postDataJSON();
    expect(route.request().headers()["x-nexus-agent-display-token"]).toBeTruthy();
    expect(body.turn_index).toBe(1);
    expect(body.idempotency_key).toBeTruthy();
    submitted++;
    display.follow_up!.items.push({ id: `follow-${submitted}`, mode: body.mode, turn_index: 1,
      dispatched_turn: null, content: body.content, status: body.mode === "steer" ? "received" : "pending", code: "" });
    return envelope(route, display.follow_up!.items.at(-1), 202);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const followUp = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await followUp.fill("/help");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Private Display commands" })).toBeVisible();
  await page.getByRole("dialog", { name: "Private Display commands" }).getByRole("button", { name: "Close", exact: true }).click();
  await followUp.fill("/unknown");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Unknown command /unknown", { exact: true })).toBeVisible();
  await expect(followUp).toHaveValue("/unknown");
  expect(submitted).toBe(0);
  await followUp.fill("/cancel");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Cancellation requested", { exact: true })).toBeVisible();
  expect(cancellations).toBe(1);
  expect(submitted).toBe(0);
  await followUp.fill("Then summarize the output");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Next turn · Saved", { exact: true })).toBeVisible();
  await page.getByLabel("While Agent is working").selectOption("steer");
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Keep the original files");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Current turn · Received by Agent · not yet applied", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/run=run-1/);
  expect(state.runs).toHaveLength(1);
  display.follow_up.items[1].status = "applied";
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Follow-up message", exact: true })).toBeVisible();
  await expect(page.getByText("Current turn · Applied by Agent", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Next turn · Saved", { exact: true })).toBeVisible();
});

test("saved follow-up returns to the composer without overwriting an answer or another draft", async ({ page }, testInfo) => {
  test.setTimeout(60000); // Four persisted page reloads plus a visual capture.
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Continue this Run", exact: true }).fill("Existing draft");
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  await page.reload();
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Remember this next");
  display.status = "input_required";
  display.execution_task.status = "input_required";
  display.interactions = [{ id: "draft-question", key: "q", kind: "text", prompt: "Which folder?", choices: [], response: {}, status: "pending", expires_at: "", answered_at: null }];
  await page.reload();
  await page.getByRole("textbox", { name: "Reply to current Run" }).fill("Answer draft");
  await expect(page.getByRole("button", { name: "Continue editing", exact: true })).toBeDisabled();
  await expect(page.getByText("Answer the current question first.", { exact: true })).toBeVisible();
  display.status = "completed";
  display.execution_task.status = "completed";
  display.interactions = [];
  await page.reload();
  const recovery = page.getByRole("region", { name: "Follow-up draft" });
  await expect(recovery).toContainText("Remember this next");
  await page.screenshot({ path: testInfo.outputPath("saved-draft.png"), fullPage: true });
  await recovery.getByRole("button", { name: "Add to message" }).click();
  const composer = page.getByRole("textbox", { name: "Continue this Run", exact: true });
  await expect(composer).toHaveValue("Existing draft\n\nRemember this next");
  await expect(composer).toBeFocused();
  await expect(recovery).toHaveCount(0);
  await expect(page).toHaveURL(/run=run-1/);
  await page.reload();
  await expect(composer).toHaveValue("Existing draft\n\nRemember this next");
});

test("uncertain delivery has one status card and checking never resends the message", async ({ page }, testInfo) => {
  test.setTimeout(60000);
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  let key = "";
  let checks = 0;
  let found = false;
  let failCheck = false;
  await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
    if (route.request().method() === "GET") {
      checks++;
      expect(new URL(route.request().url()).searchParams.get("idempotency_key")).toBe(key);
      if (checks === 1 || failCheck) return envelope(route, null, 503);
      return envelope(route, { submission: found ? display.follow_up!.items[0] : null });
    }
    posts++;
    const body = route.request().postDataJSON();
    key = body.idempotency_key;
    display.follow_up!.items = [{ id: "accepted-follow-up", mode: "queue", turn_index: 1, dispatched_turn: 2, content: body.content, status: "dispatched", code: "" }];
    display.status = "completed";
    display.execution_task.status = "completed";
    return envelope(route, null, 503);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Original message");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("region", { name: "Message delivery" })).toContainText("Delivery not confirmed");
  expect(checks).toBe(3);
  await page.reload();
  const recovery = page.getByRole("region", { name: "Message delivery" });
  await expect(recovery).toHaveCount(1);
  await expect(recovery).toContainText("Delivery not confirmed");
  expect(checks).toBe(6);
  await expect(page.getByText("Your follow-up draft is saved.", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Follow-up draft" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("delivery-recovery-mobile.png"), fullPage: true });
  failCheck = true;
  await recovery.getByRole("button", { name: "Check status", exact: true }).click();
  await expect(recovery).toContainText("Could not check delivery. Your message is still here.");
  failCheck = false;
  await recovery.getByRole("button", { name: "Check status", exact: true }).click();
  await expect(recovery).toContainText("Delivery is still unconfirmed.");
  expect(posts).toBe(1);
  found = true;
  await recovery.getByRole("button", { name: "Check status", exact: true }).click();
  await expect(recovery).toHaveCount(0);
  await expect(page.getByText("Next turn · Next turn started · Turn 2", { exact: true })).toHaveCount(0);
  expect(posts).toBe(1);
  expect(checks).toBe(9);
  await page.reload();
  await expect(recovery).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("checking confirmed delivery preserves edits and never treats equal text as proof", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  let posts = 0;
  let key = "";
  let pendingCheck: Route | undefined;
  await page.route("**/agent-runs/run-1/follow-ups/**", async route => {
    if (route.request().method() === "GET") {
      expect(new URL(route.request().url()).searchParams.get("idempotency_key")).toBe(key);
      pendingCheck = route;
      return;
    }
    posts++;
    const body = route.request().postDataJSON();
    key = body.idempotency_key;
    display.follow_up!.items = [{ id: "confirmed", mode: "queue", turn_index: 1, dispatched_turn: null, content: body.content, status: "pending", code: "" }];
    return envelope(route, null, 503);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Original instruction");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  const delivery = page.getByRole("region", { name: "Message delivery" });
  await expect(delivery).toContainText("Checking delivery…");
  await expect(page.getByText("Next turn · Saved", { exact: true })).toBeVisible();
  // Equal text in the list must not clear an unresolved request.
  await expect(delivery).toBeVisible();
  await input.fill("A newer draft");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  expect(posts).toBe(1);
  await expect.poll(() => Boolean(pendingCheck)).toBe(true);
  await envelope(pendingCheck!, { submission: display.follow_up!.items[0] });
  await expect(delivery).toHaveCount(0);
  await expect(input).toHaveValue("A newer draft");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.reload();
  await expect(input).toHaveValue("A newer draft");
  await expect(delivery).toHaveCount(0);
  expect(posts).toBe(1);
});

test("a definitive ended-turn rejection returns an editable draft instead of an uncertainty loop", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  await page.route("**/agent-runs/run-1/follow-ups/", async route => {
    display.status = "completed";
    display.execution_task.status = "completed";
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ ok: false, data: null,
      error: { code: "FOLLOW_UP_CONFLICT", message: "code: TURN_NO_LONGER_ACTIVE" } }) });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Keep for next turn");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  const draft = page.getByRole("region", { name: "Follow-up draft" });
  await expect(draft).toContainText("This turn has ended.");
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
  await draft.getByRole("button", { name: "Continue editing", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Continue this Run", exact: true })).toHaveValue("Keep for next turn");
  expect(state.submissions).toHaveLength(0);
});

test("queue-only Agent hides steering and preserves drafts across an uncertain response", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  const display = state.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [] };
  const keys: string[] = [];
  const turns: number[] = [];
  await page.route("**/agent-runs/run-1/follow-ups/", async route => {
    keys.push(route.request().postDataJSON().idempotency_key);
    turns.push(route.request().postDataJSON().turn_index);
    // The first request may have succeeded upstream before its response was lost.
    display.follow_up!.turn_index = 2;
    return envelope(route, null, 503);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("option", { name: "Guide current turn" })).toHaveCount(0);
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true });
  await input.fill("Next task");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("region", { name: "Message delivery" })).toContainText("Delivery not confirmed");
  await expect(input).toHaveValue("Next task");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry original message", exact: true }).click();
  await expect.poll(() => keys.length).toBe(2);
  expect(keys[0]).toBe(keys[1]);
  expect(turns).toEqual([1, 1]);
  await page.reload();
  await expect(input).toHaveValue("Next task");
  await expect(page.getByRole("region", { name: "Message delivery" })).toContainText("Delivery not confirmed");
  await page.getByRole("button", { name: "Retry original message", exact: true }).click();
  await expect.poll(() => keys.length).toBe(3);
  expect(keys[2]).toBe(keys[0]);
  expect(turns).toEqual([1, 1, 1]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

for (const width of [1440, 390]) {
  test(`oversized images upload an optimized copy with accurate preview at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    await mockRunOnlyDisplay(page, { images: true });
    let uploaded: Buffer | null = null;
    await page.route("**/api/v1/media/assets/", route => {
      uploaded = route.request().postDataBuffer();
      return envelope(route, { id: "optimized-image" }, 201);
    });
    await page.goto("/agents/agent-echo/private-display");
    const originalSize = await page.getByLabel("Choose attachments").evaluate(async input => {
      // Real pixels and browser encoder, not a fake compression implementation.
      const canvas = document.createElement("canvas"); canvas.width = 1200; canvas.height = 1200;
      const ctx = canvas.getContext("2d")!;
      const pixels = ctx.createImageData(1200, 1200); let seed = 12345;
      for (let i = 0; i < pixels.data.length; i += 4) {
        for (let channel = 0; channel < 3; channel++) { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; pixels.data[i + channel] = seed >>> 24; }
        pixels.data[i + 3] = 255;
      }
      ctx.putImageData(pixels, 0, 0);
      const blob = await new Promise<Blob>(resolve => canvas.toBlob(blob => resolve(blob!), "image/png"));
      const transfer = new DataTransfer(); transfer.items.add(new File([blob], "evidence.png", { type: "image/png" }));
      (input as HTMLInputElement).files = transfer.files; input.dispatchEvent(new Event("change", { bubbles: true }));
      return blob.size;
    });
    expect(originalSize).toBeGreaterThan(2 * 1024 * 1024);
    await expect(page.getByText(/Optimized copy.*Original unchanged/)).toBeVisible();
    await expect(page.getByText(/Ready to send/).first()).toBeVisible();
    expect(uploaded!.length).toBeLessThan(2 * 1024 * 1024 + 2048);
    expect(uploaded!.includes(Buffer.from('filename="evidence.webp"'))).toBe(true);
    await page.getByRole("button", { name: "Preview evidence.webp" }).click();
    await expect(page.getByRole("dialog", { name: "evidence.webp" })).toContainText("1200 × 1200");
    await page.keyboard.press("Escape");
    const stored = await page.evaluate(() => JSON.stringify(sessionStorage));
    expect(stored).not.toContain("data:image"); expect(stored).not.toContain("blob:");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const sent = page.waitForRequest(req => req.url().endsWith("/agents/agent-echo/private-runs/") && req.method() === "POST");
    await page.getByRole("button", { name: "Send message" }).click();
    expect((await sent).postDataJSON().attachments).toEqual([{ asset_id: "optimized-image" }]);
  });
}

test("unsafe oversized image remains removable and never starts an upload", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true }); let uploads = 0;
  await page.route("**/api/v1/media/assets/", route => { uploads++; return envelope(route, { id: "unexpected" }, 201); });
  await page.goto("/agents/agent-echo/private-display");
  const draft = page.getByRole("textbox", { name: /Start a new Agent Run/i }); await draft.fill("Keep my instruction");
  const bytes = Buffer.alloc(2 * 1024 * 1024 + 1);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes);
  bytes.writeUInt32BE(13, 8); bytes.write("IHDR", 12); bytes.writeUInt32BE(30000, 16); bytes.writeUInt32BE(30000, 20);
  await page.getByLabel("Choose attachments").setInputFiles({ name: "unsafe.png", mimeType: "image/png", buffer: bytes });
  await expect(page.getByRole("alert")).toContainText("dimensions are too large");
  expect(uploads).toBe(0); await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
  await page.getByRole("button", { name: "Remove image unsafe.png" }).click();
  await expect(draft).toHaveValue("Keep my instruction"); await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled();
});

for (const mode of ["queue", "steer"] as const) {
  test(`working Agent receives ${mode} image attachments only after protocol support`, async ({ page }) => {
    const state = await mockRunOnlyDisplay(page, { images: true });
    const display = state.displays.get("run-1")!;
    display.status = "running"; display.execution_task.status = "working";
    display.follow_up = { mode: "steer_and_queue", turn_index: 1, items: [], attachment_protocol: { queue: 1, steer: mode === "steer" ? 1 : 0 } };
    await page.route("**/api/v1/media/assets/", route => envelope(route, { id: "working-image" }, 201));
    let submission: Record<string, unknown> | undefined;
    await page.route("**/agent-runs/run-1/follow-ups/", route => {
      submission = route.request().postDataJSON(); return envelope(route, { id: "accepted" }, 202);
    });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    await page.getByLabel("Choose attachments").setInputFiles({ name: "working.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
    await expect(page.getByText(/Ready to send/).first()).toBeVisible();
    const input = page.getByRole("textbox", { name: "Follow-up message", exact: true }); await input.fill("Use this evidence");
    await page.getByLabel("While Agent is working").selectOption("steer");
    if (mode === "queue") {
      await expect(page.getByRole("alert")).toContainText("has not enabled attachments");
      await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
      await page.getByLabel("While Agent is working").selectOption("queue");
    }
    await input.fill("Use this evidence");
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect.poll(() => submission?.mode).toBe(mode);
    expect(submission?.attachments).toEqual([{ asset_id: "working-image" }]);
    await expect(page.getByRole("button", { name: "Remove image working.png" })).toHaveCount(0);
    await expect(input).toHaveValue("");
    expect(await page.evaluate(() => sessionStorage.getItem("nexus.composer-assets.v1"))).not.toContain("working-image");
  });
}

test("running file upload queues its reference and clears only confirmed attachments", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { files: true });
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [], attachment_protocol: { queue: 1, steer: 0 } };
  const file = { file_id: "queued-file", name: "notes.txt", size_bytes: 5, received_bytes: 0, chunk_bytes: 1024, state: "uploading" };
  await page.route("**/api/v1/agent-files/**", route => {
    const method = route.request().method(); const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/agent-files/") return envelope(route, method === "GET" ? { max_file_bytes: 1024, chunk_bytes: 1024, max_files: 8 } : file, method === "GET" ? 200 : 201);
    if (method === "PUT") file.received_bytes = 5;
    if (method === "POST") file.state = "ready";
    return envelope(route, file);
  });
  let body: Record<string, unknown> | undefined;
  await page.route("**/agent-runs/run-1/follow-ups/", route => { body = route.request().postDataJSON(); return envelope(route, { id: "accepted" }, 202); });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("notes") });
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Read the notes next");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => body?.files).toEqual(["queued-file"]);
  await expect(page.getByRole("button", { name: "Remove notes.txt" })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("nexus.composer-assets.v1"))).not.toContain("queued-file");
});

test("rejected follow-up attachment unlocks editing without an uncertainty loop", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { images: true });
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [], attachment_protocol: { queue: 1, steer: 0 } };
  await page.route("**/api/v1/media/assets/", route => envelope(route, { id: "expired-before-send" }, 201));
  await page.route("**/agent-runs/run-1/follow-ups/", route => route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ ok: false,
    error: { code: "FOLLOW_UP_CONFLICT", message: "code: FOLLOW_UP_ATTACHMENTS_UNAVAILABLE" } }) }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "expired.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  const input = page.getByRole("textbox", { name: "Follow-up message", exact: true }); await input.fill("Keep the draft");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("your message was not queued");
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
  await expect(input).toHaveValue("Keep the draft");
  await page.getByRole("button", { name: "Remove image expired.png" }).click();
  await input.fill("Text only now"); await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
});

test("uncertain attachment delivery retries immutable IDs after refresh", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { images: true });
  const display = state.displays.get("run-1")!;
  display.status = "running"; display.execution_task.status = "working";
  display.follow_up = { mode: "queue", turn_index: 1, items: [], attachment_protocol: { queue: 1, steer: 0 } };
  await page.route("**/api/v1/media/assets/", route => envelope(route, { id: "uncertain-image" }, 201));
  const posts: unknown[] = [];
  await page.route("**/agent-runs/run-1/follow-ups/**", route => {
    if (route.request().method() === "GET") return envelope(route, { submission: null });
    posts.push(route.request().postDataJSON());
    return posts.length === 1 ? envelope(route, null, 503) : envelope(route, { id: "accepted" }, 202);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "pending.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  await page.getByRole("textbox", { name: "Follow-up message", exact: true }).fill("Use original attachment");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("region", { name: "Message delivery" })).toContainText("Delivery not confirmed");
  await expect(page.getByLabel("Choose attachments")).toBeDisabled();
  await page.reload();
  await page.getByRole("button", { name: "Retry original message", exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]).toEqual(posts[0]);
  expect((posts[1] as { attachments: unknown }).attachments).toEqual([{ asset_id: "uncertain-image" }]);
  await expect(page.getByRole("region", { name: "Message delivery" })).toHaveCount(0);
});

test("attachment picker accepts multiple images without dropping later selections", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  let uploads = 0;
  await page.route("**/api/v1/media/assets/", route => envelope(route, { id: `batch-image-${++uploads}` }, 201));
  await page.goto("/agents/agent-echo/private-display");
  await page.getByLabel("Choose attachments").setInputFiles(["a.png", "b.png"].map(name => ({ name, mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") })));
  await expect(page.getByRole("button", { name: "Remove image a.png" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove image b.png" })).toBeVisible();
  await expect.poll(() => uploads).toBe(2);
  await page.getByRole("textbox", { name: /Start a new Agent Run/i }).fill("Compare both images");
  const sent = page.waitForRequest(req => req.url().endsWith("/agents/agent-echo/private-runs/") && req.method() === "POST");
  await page.getByRole("button", { name: "Send message" }).click();
  expect((await sent).postDataJSON().attachments).toEqual([{ asset_id: "batch-image-1" }, { asset_id: "batch-image-2" }]);
});

test("a verified attachment can be sent without filler text", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  await page.route("**/api/v1/media/assets/", route => envelope(route, { id: "attachment-only-image" }, 201));
  await page.goto("/agents/agent-echo/private-display");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "evidence.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled();
  const sent = page.waitForRequest(req => req.url().endsWith("/agents/agent-echo/private-runs/") && req.method() === "POST");
  await page.getByRole("button", { name: "Send message" }).click();
  const body = (await sent).postDataJSON();
  expect(body.content).toBe("");
  expect(body.attachments).toEqual([{ asset_id: "attachment-only-image" }]);
});

test("pasted screenshots use the same private attachment intake", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  let uploads = 0;
  await page.route("**/api/v1/media/assets/", route => envelope(route, { id: `pasted-${++uploads}` }, 201));
  await page.goto("/agents/agent-echo/private-display");
  await page.getByRole("textbox", { name: /Start a new Agent Run/i }).evaluate(element => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array([1, 2, 3])], "clipboard.png", { type: "image/png" }));
    element.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: transfer }));
  });
  await expect(page.getByRole("button", { name: "Remove image clipboard.png" })).toBeVisible();
  expect(uploads).toBe(1);
});

test("attachment picker uploads every selected file in order", async ({ page }) => {
  await mockRunOnlyDisplay(page, { files: true });
  let created = 0;
  const states = new Map<string, Record<string, unknown>>();
  await page.route("**/api/v1/agent-files/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/agent-files/" && request.method() === "GET") return envelope(route, { max_file_bytes: 1024, chunk_bytes: 1024, max_files: 8 });
    if (path === "/api/v1/agent-files/" && request.method() === "POST") {
      const id = `batch-file-${++created}`; const body = request.postDataJSON();
      const state = { file_id: id, name: body.name, size_bytes: body.size_bytes, received_bytes: 0, chunk_bytes: 1024, state: "uploading" };
      states.set(id, state); return envelope(route, state, 201);
    }
    const id = path.split("/").filter(Boolean).at(-1)!; const state = states.get(id)!;
    if (request.method() === "PUT") state.received_bytes = state.size_bytes;
    if (request.method() === "POST") state.state = "ready";
    return envelope(route, state);
  });
  await page.goto("/agents/agent-echo/private-display");
  await page.getByLabel("Choose attachments").setInputFiles([
    { name: "first.txt", mimeType: "text/plain", buffer: Buffer.from("first") },
    { name: "second.txt", mimeType: "text/plain", buffer: Buffer.from("second") },
  ]);
  await expect(page.getByRole("button", { name: "Remove first.txt" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove second.txt" })).toBeVisible();
  await page.getByRole("textbox", { name: /Start a new Agent Run/i }).fill("Read both");
  const sent = page.waitForRequest(req => req.url().endsWith("/agents/agent-echo/private-runs/") && req.method() === "POST");
  await page.getByRole("button", { name: "Send message" }).click();
  expect((await sent).postDataJSON().files).toEqual(["batch-file-1", "batch-file-2"]);
});

for (const width of [1440, 1024, 390]) {
  test(`unified message composer keeps attachments and footer together at ${width}px`, async ({ page }, testInfo) => {
    await mockRunOnlyDisplay(page, { images: true, files: true });
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    let upload: Route | undefined;
    await page.route("**/api/v1/media/assets/", route => { upload = route; });
    await page.goto("/agents/agent-echo/private-display");
    const form = page.getByRole("form", { name: "Message composer" });
    const input = form.getByRole("textbox");
    const plus = form.getByRole("button", { name: "Attach", exact: true });
    const send = form.getByRole("button", { name: "Send message" });
    await expect(plus).toBeVisible();
    await input.fill("Describe this image");
    await plus.click();
    const menu = page.getByRole("menu", { name: "Add attachments" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem").first()).toBeFocused();
    await page.keyboard.press("End");
    await expect(menu.getByRole("menuitem", { name: "Import from Computer" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(plus).toBeFocused();
    await plus.click();
    const chooser = page.waitForEvent("filechooser");
    await menu.getByRole("menuitem", { name: "Upload files and images" }).click();
    await (await chooser).setFiles({ name: "picture.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
    await expect.poll(() => Boolean(upload)).toBe(true);
    await expect(send).toBeDisabled();
    await expect(input).toBeEnabled();
    await input.fill("Describe this image while it uploads");
    await envelope(upload!, { id: "composer-image" }, 201);
    const attachments = form.getByRole("region", { name: "Private image attachments" });
    await expect(attachments).toContainText("Ready to send");
    await expect(send).toBeEnabled();
    const attachmentBox = (await attachments.boundingBox())!;
    const inputBox = (await input.boundingBox())!;
    const plusBox = (await plus.boundingBox())!, sendBox = (await send.boundingBox())!;
    expect(attachmentBox.y + attachmentBox.height).toBeLessThanOrEqual(inputBox.y + 1);
    expect(plusBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height);
    expect(Math.abs(plusBox.y - sendBox.y)).toBeLessThanOrEqual(1);
    expect(plusBox.x).toBeLessThan(sendBox.x);
    expect(plusBox.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`unified-composer-${width}.png`) });
    await form.getByRole("button", { name: "Remove image picture.png" }).click();
    await expect(attachments).toHaveCount(0);
    await expect(input).toHaveValue("Describe this image while it uploads");
  });
}

test("composer plus imports Computer files without rewriting or submitting the draft", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page, { files: true });
  await page.goto("/agents/agent-echo/private-display");
  const form = page.getByRole("form", { name: "Message composer" });
  const input = form.getByRole("textbox");
  await input.fill("Please review the report");
  await form.getByRole("button", { name: "Attach", exact: true }).click();
  await page.getByRole("menuitem", { name: "Import from Computer" }).click();
  const search = page.getByRole("textbox", { name: "Search Computer files" });
  await expect(search).toBeFocused();
  await search.fill("报告");
  await search.press("Enter");
  expect(state.submissions).toHaveLength(0);
  await page.getByRole("listbox", { name: "File mentions" }).getByRole("option").filter({ hasText: "报告.md" }).click();
  await expect(search).toHaveCount(0);
  await expect(input).toHaveValue("Please review the report");
  await expect(input).toBeFocused();
  await expect(form.getByRole("button", { name: "Remove 报告.md" })).toBeVisible();
  const sent = page.waitForRequest(request => request.url().endsWith("/agents/agent-echo/private-runs/") && request.method() === "POST");
  await form.getByRole("button", { name: "Send message" }).click();
  expect((await sent).postDataJSON().files).toEqual(["computer-file-1"]);
});

test("image-capable chat uploads private attachments and submits references only", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  let uploads = 0;
  await page.route("**/api/v1/media/assets/", async (route) => {
    uploads += 1;
    expect(route.request().headers()["x-nexus-tenant"]).toBe("tenant-private");
    return envelope(route, { id: "image-input-1" }, 201);
  });
  await page.goto("/agents/agent-echo/private-display");
  await expect(page.getByRole("button", { name: "Attach", exact: true })).toBeVisible();
  await page.getByLabel("Choose attachments").setInputFiles({ name: "diagram.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg/>") });
  await expect(page.getByRole("alert")).toContainText("PNG, JPEG or WebP");
  expect(uploads).toBe(0);
  await page.getByLabel("Choose attachments").setInputFiles({ name: "diagram.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  await expect(page.getByRole("button", { name: "Remove image diagram.png" })).toBeVisible();
  await page.getByRole("textbox", { name: /Start a new Agent Run/i }).fill("Describe this diagram");
  const submission = page.waitForRequest((request) => request.url().endsWith("/agents/agent-echo/private-runs/") && request.method() === "POST");
  await page.getByRole("button", { name: "Send message" }).click();
  expect((await submission).postDataJSON().attachments).toEqual([{ asset_id: "image-input-1" }]);
  await expect(page).toHaveURL(/run=run-2/);
  await expect(page.getByRole("button", { name: "Remove image diagram.png" })).toHaveCount(0);
});

test("text-only chat does not offer image uploads", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display");
  await expect(page.getByRole("textbox", { name: /Start a new Agent Run/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Attach", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Choose attachments")).toHaveCount(0);
});

for (const width of [1440, 390]) {
  test(`large file uploads resume after chunk failure and submit only references at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 960 });
    await mockRunOnlyDisplay(page, { files: true });
    const file = { file_id: "12345678-1234-4234-8234-123456789015", name: "large.bin", size_bytes: 2 * 1024**2 + 13,
      received_bytes: 0, chunk_bytes: 1024**2, sha256: "a".repeat(64), state: "uploading" };
    let failed = false;
    const offsets: number[] = [];
    await page.route("**/api/v1/agent-files/**", async route => {
      const request = route.request();
      if (request.url().endsWith("/agent-files/")) return envelope(route, request.method() === "GET" ? { max_file_bytes: 5 * 1024**3 } : file, request.method() === "POST" ? 201 : 200);
      if (request.method() === "PUT") {
        const offset = Number(request.headers()["x-nexus-upload-offset"]);
        offsets.push(offset);
        expect(request.postDataBuffer()!.length).toBeLessThanOrEqual(1024**2);
        expect(request.headers()["x-nexus-chunk-sha256"]).toMatch(/^[a-f0-9]{64}$/);
        if (offset === 1024**2 && !failed) { failed = true; return envelope(route, null, 503); }
        expect(offset).toBe(file.received_bytes);
        file.received_bytes += request.postDataBuffer()!.length;
      }
      if (request.method() === "POST") file.state = "ready";
      return envelope(route, file);
    });
    await page.goto("/agents/agent-echo/private-display");
    await page.getByLabel("Choose attachments").setInputFiles({ name: file.name, mimeType: "application/octet-stream", buffer: Buffer.alloc(file.size_bytes, 123) });
    await expect(page.getByRole("button", { name: "Retry upload" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Send message" })).toBeDisabled();
    await page.getByRole("button", { name: "Retry upload" }).click();
    await expect(page.getByText("Ready to send", { exact: true })).toBeVisible();
    expect(offsets).toEqual([0, 1024**2, 1024**2, 2 * 1024**2]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`file-upload-${width}.png`), fullPage: true });
    await page.getByRole("textbox", { name: /Start a new Agent Run/i }).fill("Inspect this file");
    const submission = page.waitForRequest(request => request.url().endsWith("/agents/agent-echo/private-runs/") && request.method() === "POST");
    await page.getByRole("button", { name: "Send message" }).click();
    const body = (await submission).postDataJSON();
    expect(body.files).toEqual([file.file_id]);
    expect(JSON.stringify(body).length).toBeLessThan(1000);
    await expect(page.getByRole("button", { name: "Remove large.bin" })).toHaveCount(0);
  });
}

test("Run file download prepares private native transfer instead of buffering a Blob", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  const file = { file_id: "12345678-1234-4234-8234-123456789015", name: "large.bin", size_bytes: 5 * 1024**3, state: "ready" };
  let prepared = false;
  await page.route(url => url.pathname === "/api/v1/agent-runs/run-1/files/", route => envelope(route, [file]));
  await page.route(`**/api/v1/agent-runs/run-1/files/${file.file_id}/download/`, async route => {
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-nexus-agent-display-token"]).toBe("token-run-1");
      prepared = true;
      return envelope(route, { url: `/api/v1/agent-runs/run-1/files/${file.file_id}/download/`, file_name: file.name });
    }
    expect(prepared).toBe(true);
    expect(route.request().resourceType()).toBe("document");
    return route.fulfill({ status: 200, body: "download", headers: { "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="large.bin"' } });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download large.bin" }).click();
  expect((await download).suggestedFilename()).toBe("large.bin");
});

test("a submitted file remains visible and downloadable in its user message", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.displays.get("run-1")!.messages[0].content_blocks = [
    { type: "markdown", text: "Inspect this" },
    { type: "file", name: "evidence.txt", content_type: "text/plain", size_bytes: 12, status: "attached", url: "/api/v1/agent-runs/run-1/files/input-1/download/" },
  ];
  let prepared = false;
  await page.route("**/api/v1/agent-runs/run-1/files/input-1/download/", route => {
    if (route.request().method() === "POST") { prepared = true; return envelope(route, { url: "/api/v1/agent-runs/run-1/files/input-1/download/", file_name: "evidence.txt" }); }
    return route.fulfill({ status: 200, body: "evidence", headers: { "Content-Type": "text/plain", "Content-Disposition": 'attachment; filename="evidence.txt"' } });
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const bubble = page.locator('[data-message-id="message-run-1"]');
  await expect(bubble).toContainText("evidence.txt");
  await expect(bubble).toContainText("Attached to this turn");
  const download = page.waitForEvent("download");
  await bubble.getByRole("button", { name: "Download evidence.txt" }).click();
  expect((await download).suggestedFilename()).toBe("evidence.txt");
  expect(prepared).toBe(true);
});

test("switching Runs during an upload does not attach the previous image", async ({ page }) => {
  await mockRunOnlyDisplay(page, { images: true });
  let release = () => {};
  const wait = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/media/assets/", async (route) => {
    await wait;
    return envelope(route, { id: "previous-run-image" }, 201);
  });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByLabel("Choose attachments").setInputFiles({ name: "old.png", mimeType: "image/png", buffer: Buffer.from("image") });
  await expect(page.getByText(/Uploading image…/)).toBeVisible();
  await page.getByRole("button", { name: "New run" }).click();
  await expect(page).toHaveURL(/private-display$/);
  release();
  await expect(page.getByRole("button", { name: "Attach", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Remove image old.png" })).toHaveCount(0);
});

test("protected Run images render, download and restore on mobile without external fetches", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page, { images: true });
  const id = "12345678-1234-4234-8234-123456789012";
  const path = `/api/v1/agent-runs/${id}/display-assets/12345678-1234-4234-8234-123456789013/`;
  const display = runDisplay(id, "Describe my image");
  (display.messages[1].content_blocks as Array<Record<string, unknown>>).push(
    { type: "image", url: path, title: "Protected result", alt: "Caller image" },
    { type: "image", url: "https://untrusted-image.invalid/tracker.png", title: "Blocked" },
  );
  control.displays.set(id, display);
  let downloads = 0;
  await page.route("**" + path, (route) => {
    expect(route.request().headers()["x-nexus-agent-display-token"]).toBe("token-" + id);
    downloads += 1;
    return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64") });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/agents/agent-echo/private-display?run=" + id);
  await expect(page.getByRole("img", { name: "Caller image" })).toBeVisible();
  await expect(page.getByText("Untrusted image reference was blocked.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download Protected result" })).toHaveAttribute("href", /^blob:/);
  await page.reload();
  await expect(page.getByRole("img", { name: "Caller image" })).toBeVisible();
  expect(downloads).toBeGreaterThanOrEqual(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
});

test("protected Browser frames open a keyboard-accessible large view with bounded zoom", async ({ page }) => {
  const framePath = "/api/v1/agent-runs/run-1/display-assets/mobile-frame/";
  await mockRunOnlyDisplay(page, { streamEvents: [{
    id: "mobile-frame", run_id: "run-1", seq: 2, type: "CUSTOM", name: "nexus.computer.frame",
    content: { screenshot_url: framePath, title: "Caller Mobile · validation complete", text: "Protected Android viewport", width: 720, height: 1280, revision: 1 },
    created_at: "2026-08-17T02:00:02Z",
  }] });
  await page.route("**" + framePath, route => route.fulfill({
    status: 200,
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=", "base64"),
  }));
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Browser", exact: true }).click();
  await page.getByRole("button", { name: "Open larger frame view" }).click();
  const dialog = page.getByRole("dialog", { name: "Caller Mobile · validation complete" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("720 × 1280 protected image", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Zoom in" }).click();
  await expect(dialog.getByLabel("Zoom level")).toHaveText("125%");
  await expect(dialog.locator('img[alt$="enlarged"]')).toHaveCSS("width", "900px");
  await dialog.getByRole("button", { name: "Fit", exact: true }).click();
  await expect(dialog.getByLabel("Zoom level")).toHaveText("Fit");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open larger frame view" })).toBeFocused();
});

test("Private Display keeps one workspace and creates an independent Run", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agent-runs/run-1/display");
  await expect(page.getByText("Workspace inspection complete").first()).toBeVisible();
  await expect(page.getByText("请检查工作区🙂")).toBeVisible();
  const workspace = page.locator("main").first();
  await workspace.evaluate((element) => element.setAttribute("data-run-workspace-instance", "stable"));
  await page.getByRole("button", { name: "New run" }).click();
  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
  await expect(page.getByText("请检查工作区🙂")).toHaveCount(0);
  await page.getByRole("textbox", { name: /Start a new Agent Run/i }).fill("第二次独立运行🚀");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page).toHaveURL(/run=run-2/);
  await expect(page.getByText("第二次独立运行🚀")).toBeVisible();
  await expect(workspace).toHaveAttribute("data-run-workspace-instance", "stable");
});

test("external Agent Project instructions are confirmed in place and exposed as an immutable Run context", async ({ page }, testInfo) => {
  await mockRunOnlyDisplay(page, { externalProjectContext: true });
  await page.goto("/agents/agent-echo/private-display");

  const composer = page.getByRole("textbox", { name: "Start a new Agent Run" });
  await expect(composer).toBeEnabled();
  await composer.fill("A draft before granting instructions");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Review instructions" }).click();
  const consent = page.getByRole("dialog", { name: "Share Project instructions" });
  await expect(consent).toContainText("Use verified sources.");
  await expect(consent).toContainText("revision 4");
  await consent.getByRole("button", { name: "Allow instructions" }).click();
  await expect(composer).toBeEnabled();
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("tab", { name: "Project", exact: true })).toHaveCount(0);
  await page.getByTitle("Open Run details · Private project").click();
  const details = page.getByRole("dialog", { name: "Run details" });
  await expect(details).toContainText("Snapshot revision");
  await expect(details).toContainText("Use verified sources.");
  await expect(details).toContainText(/Later Project edits do not change this Run/);
  await page.screenshot({ path: testInfo.outputPath("run-details-project-snapshot.png") });
});

test("completed Private Display input resumes the same Run", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const workspace = page.locator("main").first();
  await workspace.evaluate((element) => element.setAttribute("data-run-workspace-instance", "same-run"));

  await page.getByRole("textbox", { name: /Continue this Run/i }).fill("继续检查文件🙂");
  const resumeRequest = page.waitForRequest((request) => request.url().endsWith("/api/v1/agent-runs/run-1/resume/"));
  await page.getByRole("button", { name: "Send message" }).click();
  await resumeRequest;

  await expect(page).toHaveURL(/run=run-1/);
  await expect(page.getByText("继续检查文件🙂", { exact: true })).toBeVisible();
  await expect(page.getByText("Continued: 继续检查文件🙂", { exact: true })).toBeVisible();
  await expect(workspace).toHaveAttribute("data-run-workspace-instance", "same-run");
});

test("structured MCP-only tool does not replace the Private Display Run composer", async ({ page }) => {
  await mockRunOnlyDisplay(page, { structuredTool: true });
  await page.goto("/agents/agent-echo/private-display");

  await expect(page.getByText("Private Display requires exactly one available Chat tool.", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Tool arguments (JSON)" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /Start a new Agent Run/i })).toBeDisabled();
});

test("non-continuable Private Display tool starts a new Run instead of accepting follow-up text", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  display.execution_task!.continuable = false;
  display.tool_policy.continuable = false;

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("This tool runs once per Run")).toBeVisible();
  await expect(page.getByRole("textbox", { name: /Continue this Run/i })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Run message", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Start a new Run" }).click();
  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
});

test("durable cancellation is pending until the Agent acknowledges", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  display.status = "running";
  display.execution_task.status = "cancel_requested";
  display.execution_task.execution_state = "running";
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("button", { name: "Stopping · awaiting Agent" })).toBeDisabled();
});

test("long event history uses a bounded live window and paginated replay", async ({ page }) => {
  await mockRunOnlyDisplay(page, { streamEvents: Array.from({ length: 1100 }, (_, index) => ({
    id: `step-${index}`, seq: index + 2, type: "STEP_STARTED", stepName: `step-${index}`, created_at: "2026-08-17T02:00:01Z",
  })) });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("Live view: latest 1,000 events.")).toBeVisible();
  await page.getByRole("button", { name: "Earlier events" }).click();
  await expect(page.getByText("Event history · live updates paused.")).toBeVisible();
  await page.getByRole("button", { name: "Return to live" }).click();
  await expect(page.getByText("Live view: latest 1,000 events.")).toBeVisible();
});

test("failed Private Display Run resumes on the same URL after runtime recovery", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  const summary = control.runs[0];
  display.status = "failed";
  display.completed_at = "2026-08-17T02:00:03Z";
  display.execution_task!.status = "failed";
  display.execution_task!.error_code = "OPENWRT_CONNECTION_LOST";
  display.failure = { domain: "agent", code: "OPENWRT_CONNECTION_LOST", title: "Agent unavailable", message: "The Agent connection was lost.", recovery_hint: "Check Agent availability.", actions: ["check_status", "continue_chat"], outcome_unknown: true, automatic_retry: false };
  display.execution_task!.result = { message: "The Agent connection was lost." };
  summary.status = "failed";
  summary.completed_at = display.completed_at;
  control.setRuntimeAvailable(false);

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByRole("alert").first()).toContainText("The Agent connection was lost.");
  const composer = page.getByRole("textbox", { name: /Continue this Run/i });
  await expect(composer).toBeEnabled();
  await composer.fill("continue after reconnect");
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
  await expect(page.getByText("The Agent is offline. Waiting for it to reconnect.", { exact: true })).toBeVisible();

  control.setRuntimeAvailable(true);
  // Readiness polls every 5 seconds. Allow two cycles plus rendering under parallel QA load.
  await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled({ timeout: 15_000 });
  await expect(composer).toHaveValue("continue after reconnect");
  const resumeRequest = page.waitForRequest((request) => request.url().endsWith("/api/v1/agent-runs/run-1/resume/"));
  await page.getByRole("button", { name: "Send message" }).click();
  await resumeRequest;

  await expect(page).toHaveURL(/run=run-1/);
  await expect(page.getByText("continue after reconnect", { exact: true })).toBeVisible();
  await expect(page.getByText("Continued: continue after reconnect", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const item of [
  { domain: "agent", code: "HANDLER_FAILED", title: "Agent handler failed", actions: ["check_status", "continue_chat"], button: "Check status" },
  { domain: "cloud", code: "RUN_CONTEXT_TLS_FAILED", title: "Cloud trust could not be verified", actions: ["check_status"], button: "Check status" },
  { domain: "computer", code: "COMPUTER_RUNTIME_OFFLINE", title: "Computer needs attention", actions: ["manage_computer", "check_status"], button: "Review Computer" },
  { domain: "browser", code: "BROWSER_ACTION_FAILED", title: "Browser needs attention", actions: ["view_browser", "continue_chat"], button: "Inspect Browser" },
  { domain: "business", code: "TOOL_EXECUTION_FAILED", title: "Task could not be completed", actions: ["continue_chat"], button: "Revise instruction" },
] as const) {
  test(`failure recovery classifies ${item.domain} without automatic replay`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const control = await mockRunOnlyDisplay(page);
    const display = control.displays.get("run-1")!;
    display.status = "failed";
    display.execution_task.status = "failed";
    display.execution_task.error_code = item.code;
    display.execution_task.result = { message: "Authorization: Bearer private-diagnostic-token" };
    display.failure = { domain: item.domain, code: item.code, title: item.title, message: "A scoped operation failed.",
      recovery_hint: "Review this component before continuing.", actions: [...item.actions], outcome_unknown: true, automatic_retry: false };
    const mutations: string[] = [];
    page.on("request", request => { if (request.method() === "POST" && /\/(resume|private-runs|computer)\/$/.test(new URL(request.url()).pathname)) mutations.push(request.url()); });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const notice = page.getByRole("region", { name: "Run failure recovery" });
    await expect(notice).toContainText(item.title);
    await expect(notice).not.toContainText("private-diagnostic-token");
    await expect(page.getByText("The connection was lost. Continue this Run after the Agent reconnects.")).toHaveCount(0);
    const action = notice.getByRole("button", { name: item.button, exact: true });
    await expect(action).toBeEnabled();
    expect((await action.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await action.click();
    if (item.domain === "computer") await expect(page.getByRole("dialog", { name: "Change Computer for this Run" })).toBeVisible();
    if (item.domain === "browser") { await expect(page.getByText("No Browser frame", { exact: true })).toBeVisible(); await expect(notice).toBeVisible(); }
    if (item.domain === "business") await expect(page.getByRole("textbox", { name: "Continue this Run" })).toBeFocused();
    expect(mutations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`${item.domain}-failure-390.png`), fullPage: true });
  });
}

test("lost Browser starts a fresh empty Run without replaying instructions", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  display.status = "failed";
  display.failure = { domain: "browser", code: "BROWSER_SESSION_LOST", title: "Browser session ended", message: "The previous session is unavailable.", recovery_hint: "Previous page state cannot be restored.", actions: ["view_browser", "new_run"], outcome_unknown: true, automatic_retry: false };
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("button", { name: "Start a fresh Run", exact: true }).click();
  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
  await expect(page.getByRole("textbox", { name: "Start a new Agent Run" })).toBeEmpty();
  expect(control.runs).toHaveLength(1);
});

test("legacy unknown failure does not expose raw diagnostics and failed copy has a safe fallback", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  display.status = "failed";
  display.execution_task.error_code = "MCP_TASK_FAILED";
  display.execution_task.result = { message: "secret-token https://private.internal" };
  await page.addInitScript(() => { Object.defineProperty(navigator, "clipboard", { value: { writeText: () => Promise.reject(new Error("denied")) }, configurable: true }); });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const notice = page.getByRole("region", { name: "Run failure recovery" });
  await expect(notice).toContainText("Failure source not confirmed");
  await page.getByRole("button", { name: "Copy failure reference" }).click();
  await expect(notice).toContainText("Select this reference manually");
  await expect(notice).toContainText("Run: run-1");
  await expect(notice).not.toContainText("secret-token");
  await expect(notice).not.toContainText("private.internal");
});

test("Cloud display outage preserves history and reconnects without resuming the Agent", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  let unavailable = false;
  await page.route("**/api/v1/agent-runs/run-1/display/", route => unavailable ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "UNAVAILABLE", message: "private-server-detail" } }) }) : route.fallback());
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("请检查工作区🙂", { exact: true })).toBeVisible();
  unavailable = true;
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByText(/Cloud connection unavailable/)).toBeVisible({ timeout: 30000 });
  await expect(page.getByText("请检查工作区🙂", { exact: true })).toBeVisible();
  await expect(page.getByText("This Run does not exist or belongs to another caller.")).toHaveCount(0);
  unavailable = false;
  await page.getByRole("button", { name: "Reconnect display", exact: true }).click();
  await expect(page.getByText(/Cloud connection unavailable/)).toHaveCount(0);
  expect(control.displays.get("run-1")!.messages).toHaveLength(2);
});

test("Private Display returns to its Marketplace origin", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.evaluate(() => {
    window.history.replaceState(
      { ...window.history.state, usr: { returnTo: "/marketplace/agents/agent-echo?price=paid" } },
      "",
      window.location.href,
    );
  });
  await page.reload();
  await expect(page.getByText("Workspace inspection complete").first()).toBeVisible();
  await page.getByRole("button", { name: "Return to previous page" }).click();
  await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?price=paid$/);
});

for (const device of ["mobile"] as const) {
  test(`Private Display ${device} change returns to the caller Marketplace entry`, async ({ page }) => {
    await mockRunOnlyDisplay(page, { mobileRequired: true });
    const managementRequests: string[] = [];
    page.on("request", request => { if (/\/api\/v1\/agents\/agent-echo\/$/.test(new URL(request.url()).pathname)) managementRequests.push(request.url()); });
    await page.goto("/marketplace/agents/agent-echo?price=paid");
    await page.getByRole("link", { name: "Private Display", exact: true }).click();
    await page.getByRole("button", { name: /^Workspace inspection complete.*completed/i }).click();
    await page.reload();
    await chooseComposerDevice(page, "Attach Mobile");
    await expect(page).toHaveURL(new RegExp(`/marketplace/agents/agent-echo\\?price=paid&attach=${device}&return=private-display$`));
    const dialog = page.getByRole("dialog", { name: "Attach Mobile", exact: true });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?price=paid$/);
    await page.reload();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(managementRequests).toEqual([]);
  });
}

test("attachment navigation rejects missing, external, unrelated and cross-Agent origins", async ({ page }) => {
  for (const returnTo of [undefined, "https://other.example/", "//other.example/", "/\\other.example/", "/agents/other/publish", "/agents/agent-echo/settings", "/agents/agent-echo/private-display", "/agent-runs/old/display", "/settings"]) {
    expect(privateDisplayReturnTo("agent-echo", { returnTo }, enterpriseAgentDisplayNavigation)).toBe("/marketplace/agents/agent-echo");
  }
  expect(privateDisplayAttachmentUrl("agent-echo", { returnTo: "/agents/agent-echo/publish?q=test&attach=mobile&return=private-display#listing" }, "computer", enterpriseAgentDisplayNavigation))
    .toBe("/agents/agent-echo/publish?q=test&attach=computer&return=private-display#listing");
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display");
  await chooseComposerDevice(page, "Change Computer");
  await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?attach=computer&return=private-display$/);
  await expect(page.getByRole("dialog", { name: "Attach default Computer", exact: true })).toBeVisible();
});

test("Run History supports search, rename, delete and Plan belongs to Run Context", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("button", { name: "Search and filter runs" }).click();
  await page.getByPlaceholder("Search runs").fill("Workspace");
  await expect(page.getByText("Workspace inspection complete").first()).toBeVisible();
  await page.getByLabel("Actions for Workspace inspection complete").click();
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("My private run");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByPlaceholder("Search runs").fill("");
  await expect(page.getByText("My private run").first()).toBeVisible();
  await page.getByRole("tab", { name: "Plan" }).click();
  await expect(page.getByText("Inspect workspace", { exact: true })).toBeVisible();
  await page.getByLabel("Actions for My private run").click();
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await page.getByRole("button", { name: "Remove run" }).click();
  await expect(page).not.toHaveURL(/run=run-1/);
});

test("Run rail groups actionable work and remembers its desktop width", async ({ page }, testInfo) => {
  const state = await mockRunOnlyDisplay(page, { historyMetadata: true });
  state.runs.push(runSummary("running-run", "Background task", "running"), runSummary("question-run", "Needs confirmation", "input_required"), runSummary("failed-run", "Failed task", "failed"));
  state.displays.set("running-run", runDisplay("running-run", "Working", "running"));
  state.displays.set("question-run", runDisplay("question-run", "Question", "input_required"));
  state.displays.set("failed-run", runDisplay("failed-run", "Failure", "failed"));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  const history = page.getByLabel("Run history", { exact: true });
  await expect(history.getByRole("heading", { name: "Needs input", exact: true })).toBeVisible();
  await expect(history.getByRole("heading", { name: "Running", exact: true })).toBeVisible();
  await expect(history.getByRole("heading", { name: "Failed", exact: true })).toBeVisible();
  await expect(history.getByRole("heading", { name: "Recent", exact: true })).toBeVisible();
  const handle = page.getByRole("separator", { name: "Resize Run history" });
  const before = (await history.boundingBox())!.width;
  await handle.focus();
  await handle.press("ArrowRight");
  await expect.poll(async () => (await history.boundingBox())!.width).toBeGreaterThan(before);
  const resized = (await history.boundingBox())!.width;
  await page.screenshot({ path: testInfo.outputPath("run-rail-desktop.png"), fullPage: true });
  await page.reload();
  await expect.poll(async () => (await history.boundingBox())!.width).toBeGreaterThanOrEqual(resized - 1);
});

test("Workspace context switches the current Run working folder", async ({ page }) => {
  const control = await mockRunOnlyDisplay(page);
  const display = control.displays.get("run-1")!;
  display.status = "input_required";
  display.completed_at = null;
  display.execution_task!.status = "input_required";
  display.interactions = [{
    id: "interaction-1",
    key: "folder",
    kind: "confirm",
    prompt: "Choose a folder",
    choices: [],
    response: {},
    status: "pending",
    expires_at: "2026-08-17T03:00:00Z",
    answered_at: null,
  }];
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await expect(page.getByText("Choose a folder", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Workspace" }).click();
  await expect(page.getByText("Workspace /", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "documents" }).click();
  const switchRequest = page.waitForRequest((request) => request.url().endsWith("/api/v1/agent-runs/run-1/computer/") && request.method() === "PATCH");
  await page.getByRole("button", { name: "Use folder" }).click();
  await switchRequest;
  await expect(page.getByText("Workspace / documents", { exact: true })).toBeVisible();
});

for (const width of [1440, 390]) {
  test(`Workspace selects any caller device folder with confirmation at ${width}px`, async ({ page }) => {
    const state = await mockRunOnlyDisplay(page);
    const display = state.displays.get("run-1")!;
    display.status = "completed"; display.execution_task!.status = "completed";
    Object.assign(display.computer, { directory_selection_available: true, workspace_root: "C:/NexusWorkspace", workspace_cwd: ".", revision: 0 });
    let grants = 0;
    await page.route("**/api/v1/agent-runs/run-1/computer/**", async route => {
      const req = route.request(); const url = new URL(req.url());
      let path = url.searchParams.get("path") || "C:/NexusWorkspace";
      if (req.method() === "PATCH") {
        const data = req.postDataJSON();
        expect(data).toMatchObject({ device: true, confirmed: true, path: "D:/项目", expected_root: "C:/NexusWorkspace", expected_revision: 0 });
        grants++; display.computer.workspace_root = path = data.path;
      } else expect(url.searchParams.get("device")).toBe("1");
      return envelope(route, { device: true, path, workspace_root: display.computer.workspace_root, cwd: ".", parent: path === "D:/" ? null : "D:/", directories: path === "D:/" ? [{ name: "项目", path: "D:/项目" }] : [], roots: [{ name: "D:", path: "D:/" }] });
    });
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    if (width === 390) await page.getByRole("tab", { name: "Context", exact: true }).click();
    await page.getByRole("tab", { name: "Workspace", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Device folder path" })).toHaveValue("C:/NexusWorkspace");
    await page.getByRole("button", { name: "D:", exact: true }).click();
    await page.getByRole("button", { name: "项目", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Device folder path" })).toHaveValue("D:/项目");
    expect(grants).toBe(0);
    await page.getByRole("button", { name: "Use folder", exact: true }).click();
    await expect.poll(() => grants).toBe(1);
    await expect(page.getByText("D:/项目", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("Shell output is selectable, copyable and stable across live event boundaries", async ({ page }) => {
  const expected = "$ Get-ChildItem\nfirst\nsecond\n\n中文\nProcess exited with code 0";
  await mockRunOnlyDisplay(page, {
    streamEvents: [{
      id: "sdk-shell-duplicate",
      run_id: "run-1",
      seq: 2,
      type: "CUSTOM",
      name: "nexus.computer.log",
      content: { stream: "stdout", message: "first\r\n\r\nsecond\r\n\r\n中文" },
      created_at: "2026-08-17T02:00:02Z",
    }],
    terminalEvents: [
      { seq: 1, kind: "command", command_id: "command-1", data: "Get-ChildItem", exit_code: null, created_at: "2026-08-17T02:00:01Z" },
      { seq: 2, kind: "stdout", command_id: "command-1", data: "first\r\n", exit_code: null, created_at: "2026-08-17T02:00:02Z" },
      { seq: 3, kind: "stdout", command_id: "command-1", data: "\r\nsecond\r\n\r\n\u001b[3", exit_code: null, created_at: "2026-08-17T02:00:02Z" },
      { seq: 4, kind: "stdout", command_id: "command-1", data: "2m中文\u001b[0m\r\n", exit_code: null, created_at: "2026-08-17T02:00:02Z" },
      { seq: 5, kind: "exit", command_id: "command-1", data: "", exit_code: 0, created_at: "2026-08-17T02:00:03Z" },
    ],
  });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (value: string) => { (window as Window & { __copiedShell?: string }).__copiedShell = value; } },
    });
  });

  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Shell" }).click();
  const transcript = page.getByLabel("Read-only Shell transcript");
  await expect(transcript).toContainText("$ Get-ChildItem");
  await expect(transcript).toContainText("first\nsecond\n\n中文");
  await expect(transcript).toContainText("Exit 0");
  expect(await transcript.evaluate((element) => getComputedStyle(element).userSelect)).not.toBe("none");

  await page.getByRole("button", { name: "Copy Shell output" }).click();
  await expect.poll(() => page.evaluate(() => (window as Window & { __copiedShell?: string }).__copiedShell)).toBe(expected);
  await expect(page.getByText("Shell output copied")).toBeVisible();
});

for (const width of [1440, 390]) {
  test(`long Shell output scrolls inside its panel at ${width}px`, async ({ page }, testInfo) => {
    const terminalEvents = Array.from({ length: 40 }, (_, i) => [
      { seq: i * 3 + 1, kind: "command", command_id: `long-${i}`, data: `echo batch-${i}`, exit_code: null, created_at: "2026-08-17T02:00:01Z" },
      { seq: i * 3 + 2, kind: "stdout", command_id: `long-${i}`, data: `中文输出 ${i}\n${"long-output-".repeat(80)}\nline two\nline three\n`, exit_code: null, created_at: "2026-08-17T02:00:02Z" },
      { seq: i * 3 + 3, kind: "exit", command_id: `long-${i}`, data: "", exit_code: 0, created_at: "2026-08-17T02:00:03Z" },
    ]).flat();
    await mockRunOnlyDisplay(page, { terminalEvents });
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    await page.getByRole("tab", { name: "Shell", exact: true }).click();
    const viewport = page.getByLabel("Read-only Shell transcript");
    await expect(viewport).toContainText("中文输出 39");
    const size = await viewport.evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight, bottom: el.getBoundingClientRect().bottom }));
    expect(size.height).toBeGreaterThan(100);
    expect(size.height).toBeLessThan(700);
    expect(size.scroll).toBeGreaterThan(size.height * 3);
    const composer = page.getByRole("form", { name: "Message composer" });
    expect(size.bottom).toBeLessThanOrEqual((await composer.boundingBox())!.y + 2);
    await viewport.hover();
    await viewport.evaluate(el => { el.scrollTop = 1000; });
    await page.mouse.wheel(0, -500);
    await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBeLessThan(900);
    await page.mouse.wheel(0, 700);
    await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBeGreaterThan(900);
    await viewport.focus();
    await page.keyboard.press("Control+Home");
    await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBe(0);
    await page.getByRole("tab", { name: "Chat", exact: true }).last().click();
    await page.getByRole("tab", { name: "Shell", exact: true }).click();
    await expect.poll(() => viewport.evaluate(el => el.scrollTop)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "Wrap", exact: true }).click();
    const output = viewport.locator("pre").first();
    expect(await output.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
    await output.evaluate(el => { el.scrollLeft = 500; });
    expect(await output.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
    await page.screenshot({ path: testInfo.outputPath(`shell-scroll-${width}.png`) });
  });
}

test("Shell groups commands and offers search, wrap and export controls", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockRunOnlyDisplay(page, { terminalEvents: [
    { seq: 1, kind: "command", command_id: "one", data: "Get-Location", exit_code: null, created_at: "2026-08-17T02:00:01Z" },
    { seq: 2, kind: "stdout", command_id: "one", data: "C:\\workspace\r\n", exit_code: null, created_at: "2026-08-17T02:00:02Z" },
    { seq: 3, kind: "exit", command_id: "one", data: "", exit_code: 0, created_at: "2026-08-17T02:00:03Z" },
    { seq: 4, kind: "command", command_id: "two", data: "Write-Error broken", exit_code: null, created_at: "2026-08-17T02:00:04Z" },
    { seq: 5, kind: "stderr", command_id: "two", data: "broken\r\n", exit_code: null, created_at: "2026-08-17T02:00:05Z" },
    { seq: 6, kind: "exit", command_id: "two", data: "", exit_code: 1, created_at: "2026-08-17T02:00:06Z" },
  ] });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Shell" }).click();

  await expect(page.getByText("Caller Computer · powershell · read only")).toBeVisible();
  await expect(page.getByText("cwd .", { exact: true })).toBeVisible();
  await expect(page.getByText("Exit 1", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Search Shell transcript" }).fill("Get-Location");
  await expect(page.getByText("$ Get-Location", { exact: true })).toBeVisible();
  await expect(page.getByText("$ Write-Error broken", { exact: true })).toHaveCount(0);
  const wrap = page.getByRole("button", { name: "Wrap", exact: true });
  await expect(wrap).toHaveAttribute("aria-pressed", "true");
  await wrap.click();
  await expect(wrap).toHaveAttribute("aria-pressed", "false");
  const download = page.getByRole("button", { name: "Download Shell transcript" });
  await expect(download).toBeEnabled();
  expect((await download.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test("Shell backfills more than one thousand persisted Terminal events without silent loss", async ({ page }) => {
  const terminalEvents = [
    { seq: 1, kind: "command", command_id: "large", data: "stream-output", exit_code: null, created_at: "2026-08-17T02:00:00Z" },
    ...Array.from({ length: 1_103 }, (_, index) => ({ seq: index + 2, kind: "stdout", command_id: "large", data: `record-${index + 1}\n`, exit_code: null, created_at: "2026-08-17T02:00:01Z" })),
    { seq: 1_105, kind: "exit", command_id: "large", data: "", exit_code: 0, created_at: "2026-08-17T02:00:02Z" },
  ];
  await mockRunOnlyDisplay(page, { terminalEvents });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Shell" }).click();

  await expect(page.getByLabel("Read-only Shell transcript")).toContainText("record-1103", { timeout: 10_000 });
  await expect(page.getByText("Exit 0", { exact: true })).toBeVisible();
});

test("Shell copy action remains visible while output is waiting", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Shell" }).click();

  const copy = page.getByRole("button", { name: "Copy Shell output" });
  await expect(copy).toBeVisible();
  await expect(copy).toBeDisabled();
  await expect(copy).toHaveAttribute("title", "No Shell output to copy yet");
});

test("Private Display returns through Marketplace Mobile attachment and preserves its entry", async ({ page }) => {
  await mockRunOnlyDisplay(page, { mobileRequired: true });
  await page.goto("/agents/agent-echo/private-display");
  await expect(page.getByText("Allow Mobile permissions.").first()).toBeVisible();
  await chooseComposerDevice(page, "Attach Mobile");
  await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?attach=mobile&return=private-display$/);
  const dialog = page.getByRole("dialog", { name: "Attach Mobile", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Allow requested permissions", exact: true }).click();
  await dialog.getByRole("button", { name: "Attach Mobile", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Change Mobile", exact: true }).getByText("Mobile attached", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
  await expect(page.getByText("Allow Mobile permissions.")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: /Start a new Agent Run/i })).toBeEnabled();
  await chooseComposerDevice(page, "Change Mobile");
  await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?attach=mobile&return=private-display$/);
  await expect(page.getByRole("dialog", { name: "Change Mobile", exact: true })).toBeVisible();
});

test("Computer attachment returns to Private Display without losing Marketplace filters", async ({ page }) => {
  await mockRunOnlyDisplay(page);
  await page.goto("/marketplace/agents/agent-echo?price=paid&q=workspace");
  await page.getByRole("link", { name: "Private Display", exact: true }).click();
  await chooseComposerDevice(page, "Change Computer");
  const dialog = page.getByRole("dialog", { name: "Attach default Computer", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Attach Computer", exact: true }).click();
  await page.getByRole("dialog", { name: "Change default Computer", exact: true }).getByRole("button", { name: "Done", exact: true }).click();
  await expect(page).toHaveURL(/\/agents\/agent-echo\/private-display$/);
  await page.reload();
  await chooseComposerDevice(page, "Change Computer");
  await expect(page).toHaveURL(/\/marketplace\/agents\/agent-echo\?price=paid&q=workspace&attach=computer&return=private-display$/);
  await expect(page.getByRole("dialog", { name: "Change default Computer", exact: true })).toBeVisible();
});

for (const width of [1440, 390]) {
  test(`finished Run changes its own Computer and continues the same Run at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 960 });
    const state = await mockRunOnlyDisplay(page);
    state.displays.get("run-1")!.computer.online = false;
    let defaultWrites = 0;
    page.on("request", request => { if (request.method() === "POST" && request.url().endsWith("/computer-bindings/")) defaultWrites++; });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    if (width === 390) await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await expect(page.getByText(/Old Computer is offline/).first()).toBeVisible();
    await chooseComposerDevice(page, "Change Computer");
    const dialog = page.getByRole("dialog", { name: "Change Computer for this Run", exact: true });
    await expect(dialog.getByText(/files and browser sessions are not migrated/)).toBeVisible();
    await dialog.getByRole("button", { name: "Use for next turn", exact: true }).click();
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await expect(page).toHaveURL(/private-display\?run=run-1$/);
    await expect(page.getByRole("textbox", { name: /Continue this Run/i })).toBeEnabled();
    await expect(page.getByText("请检查工作区🙂", { exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: /Continue this Run/i }).fill("Use the new Computer");
    const continued = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/agent-runs/run-1/resume/"));
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await continued;
    await expect(page.getByText("Continued: Use the new Computer", { exact: true })).toBeVisible();
    expect(defaultWrites).toBe(0);
    expect(state.displays.get("run-1")!.computer.revision).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("running Run asks the caller to stop before changing Computer", async ({ page }) => {
  const state = await mockRunOnlyDisplay(page);
  state.displays.get("run-1")!.status = "running";
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await chooseComposerDevice(page, "Change Computer");
  await expect(page.getByText(/Stop this Run and wait/)).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.displays.get("run-1")!.computer.revision).toBe(0);
});

test("Run details shows Mobile context without a duplicate Mobile tab on small screens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await mockRunOnlyDisplay(page, { mobileRequired: true });
  Object.assign(state.displays.get("run-1")!.mobile, { attached: true, name: "Caller phone A", status: "online", capabilities: ["mobile.observe"] });
  await page.goto("/agents/agent-echo/private-display?run=run-1");
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Mobile", exact: true })).toHaveCount(0);
  await page.getByTitle("Open Run details · Private project").click();
  const details = page.getByRole("dialog", { name: "Run details" });
  await expect(details).toContainText("Caller phone A");
  await expect(details).toContainText("observe");
});

for (const width of [1440, 390]) {
  test(`chat shows Thinking and inline stop without runtime tables at ${width}px`, async ({ page }, testInfo) => {
    const state = await mockRunOnlyDisplay(page, { advancedComposer: true });
    const display = state.displays.get("run-1")!;
    display.status = "running"; display.execution_task!.status = "working";
    display.messages.push({ ...assistantMessage, id: "preparing", content: "Pi is preparing your private workspace.", content_blocks: [{ type: "markdown", text: "Pi is preparing your private workspace." }] });
    display.messages[1].content_blocks.push({ type: "fields", items: [{ label: "Status", value: "completed" }, { label: "Tools Executed", value: "1" }] });
    display.messages[1].content_blocks.push({ type: "markdown", text: "| Example | Value |\n| --- | --- |\n| Answer | 42 |" });
    let calls = 0;
    await page.route("**/api/v1/agent-runs/run-1/cancel/", async route => {
      calls++;
      if (calls === 1) {
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ ok: false, error: { code: "UNAVAILABLE", message: "Retry stopping." } }) }); return;
      }
      display.execution_task!.status = "cancel_requested";
      await envelope(route, state.runs[0]);
    });
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/agents/agent-echo/private-display?run=run-1");
    const history = page.getByLabel("Chat history", { exact: true });
    await expect(history.locator('[data-content-block="fields"]')).toHaveCount(0);
    await expect(history.getByText("Pi is preparing your private workspace.")).toHaveCount(0);
    await expect(history.getByRole("table")).toContainText("42");
    await expect(history.getByRole("status", { name: "Thinking", exact: true })).toBeVisible();
    const composer = page.getByRole("form", { name: "Message composer" });
    const draft = composer.getByRole("textbox");
    await draft.fill("Keep my draft");
    const stop = composer.getByRole("button", { name: "Stop response", exact: true });
    await expect(stop).toBeEnabled();
    await stop.click();
    await expect(page.getByText("Retry stopping.", { exact: true })).toBeVisible();
    await expect(stop).toBeEnabled();
    await expect(draft).toHaveValue("Keep my draft");
    await stop.click();
    await expect(stop).toBeDisabled();
    await expect(history.getByRole("status", { name: "Stopping", exact: true })).toBeVisible();
    expect(calls).toBe(2);
    await page.screenshot({ path: testInfo.outputPath(`inline-stop-${width}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    display.status = "failed"; display.execution_task!.status = "cancelled";
    await expect(history.getByText("Stopped", { exact: true })).toBeVisible();
    await expect(stop).toHaveCount(0);
    await expect(draft).toHaveValue("Keep my draft");
    await expect(page.getByText("Failure source not confirmed", { exact: true })).toHaveCount(0);
  });
}
