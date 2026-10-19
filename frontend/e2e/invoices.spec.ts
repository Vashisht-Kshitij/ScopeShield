import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS || fileURLToPath(new URL("../../.local/day9/", import.meta.url));
async function login(page: Page, username = "qa.freelancer") {
  const password = process.env.SCOPESHIELD_E2E_PASSWORD;
  if (!password) throw new Error("Use the isolated browser-test runner");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
}
async function review(scope: Page | Locator) {
  await scope.getByLabel("Reviewer name", { exact: true }).fill("Synthetic invoice reviewer");
  await scope.getByLabel("Review note", { exact: true }).fill("Reviewed the source and this explicit synthetic action");
}
async function audit(page: Page, name: string) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await fs.mkdir(artifacts, { recursive: true });
  await fs.writeFile(`${artifacts}/axe-${name}.json`, JSON.stringify(result, null, 2));
  expect.soft(result.violations.map((row) => ({ id: row.id, targets: row.nodes.map((node) => node.target) }))).toEqual([]);
}
async function checkDialogFocus(page: Page, dialog: Locator) {
  for (const key of ["Tab", "Shift+Tab"]) {
    for (let count = 0; count < 12; count++) {
      await page.keyboard.press(key);
      expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    }
  }
}

test("approved milestone preview, one mocked draft, refresh, explicit mocked send and private invoice access", async ({ page }) => {
  test.setTimeout(100_000);
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  let creates = 0, sends = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/milestones\/[^/]+\/invoice$/.test(new URL(request.url()).pathname)) creates++;
    if (request.method() === "POST" && /\/milestones\/[^/]+\/invoice\/send$/.test(new URL(request.url()).pathname)) sends++;
  });
  await page.goto("/demos"); await login(page);
  await page.getByRole("button", { name: "Load Milestone approval and invoice", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Postcard sketch", exact: true })).toBeVisible();
  const contractId = new URL(page.url()).pathname.split("/").pop()!;
  await page.getByRole("button", { name: "Approve milestone", exact: true }).click();
  const approval = page.getByRole("dialog"); await review(approval);
  await approval.getByRole("checkbox").check();
  await approval.getByRole("button", { name: "Confirm approve milestone", exact: true }).click();
  await expect(approval).not.toBeVisible();
  await page.getByRole("tab", { name: "Invoices", exact: true }).click();
  await expect(page.getByLabel("Approved milestone to invoice")).toContainText("Postcard sketch");
  await page.getByRole("button", { name: "Preview invoice draft", exact: true }).click();
  const preview = page.getByRole("dialog");
  await expect(preview.getByText("buyer@personal.example.com", { exact: true })).toBeVisible();
  await expect(preview.getByText("USD 1.00", { exact: true })).toBeVisible();
  await checkDialogFocus(page, preview);
  await audit(page, "invoice-preview");
  await page.screenshot({ path: `${artifacts}/invoice-preview.jpg`, fullPage: false });
  await review(preview);
  await preview.getByRole("checkbox", { name: "I reviewed the discrepancies and intend this exact partial draft." }).check();
  await preview.getByLabel("Discrepancy explanation").fill("Only the first reviewed USD 1 milestone is selected");
  await preview.getByRole("checkbox", { name: "I confirm creating one sandbox draft for this milestone and recipient." }).check();
  expect(creates).toBe(0); expect(sends).toBe(0);
  await preview.getByRole("button", { name: "Create sandbox draft", exact: true }).click();
  await expect(preview).not.toBeVisible();
  const invoice = page.getByRole("region", { name: "Milestone invoice" });
  const id = await invoice.locator(".record-identifier").innerText();
  expect(id).toMatch(/^INV2-MOCK-/);
  await expect(invoice.getByText("PayPal status: DRAFT", { exact: true })).toBeVisible();
  await expect(invoice.getByText(/Mocked PayPal responses/)).toBeVisible();
  await invoice.getByRole("button", { name: "Refresh PayPal status", exact: true }).click();
  await expect(invoice.getByText("Provider status checked; no new invoice was created.", { exact: true })).toBeVisible();
  expect(creates).toBe(1); expect(sends).toBe(0);
  await audit(page, "invoice-desktop");
  await page.screenshot({ path: `${artifacts}/invoice-desktop.jpg`, fullPage: false });
  await page.reload();
  await expect(page.locator(".record-identifier")).toHaveText(id);
  expect(creates).toBe(1);
  await page.getByRole("button", { name: "Review sending email", exact: true }).click();
  const sending = page.getByRole("dialog");
  expect(sends).toBe(0);
  await expect(sending.getByRole("button", { name: "Confirm sending sandbox email", exact: true })).toBeDisabled();
  await review(sending); await sending.getByRole("checkbox").check();
  await checkDialogFocus(page, sending);
  await audit(page, "invoice-send-dialog");
  await page.screenshot({ path: `${artifacts}/invoice-send-dialog.jpg`, fullPage: false });
  await sending.getByRole("button", { name: "Confirm sending sandbox email", exact: true }).click();
  await expect(sending).not.toBeVisible();
  await expect(page.getByText("PayPal status: SENT", { exact: true })).toBeVisible();
  await page.getByText("Invoice audit history", { exact: true }).click();
  await expect(page.getByText("Send accepted", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review sending email", exact: true })).toBeDisabled();
  expect(creates).toBe(1); expect(sends).toBe(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("tab", { name: "Invoices", exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await audit(page, "invoice-mobile");
  await page.screenshot({ path: `${artifacts}/invoice-mobile.jpg`, fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByText("Client access to this project", { exact: true }).click();
  await page.getByLabel("Client username", { exact: true }).fill("qa.client");
  await page.getByRole("checkbox", { name: "I reviewed the project contents and intend to share them with this client." }).check();
  await page.getByRole("button", { name: "Share project", exact: true }).click();
  await expect(page.getByText("Project shared with the selected client.", { exact: true })).toBeVisible();
  const saved = await page.request.get(`http://127.0.0.1:8001/api/contracts/${contractId}`);
  const milestoneId = (await saved.json()).milestones[0].id;
  await page.getByRole("button", { name: "Sign out", exact: true }).click(); await login(page, "qa.client");
  await expect(page.getByRole("heading", { name: "Shared projects", exact: true })).toBeVisible();
  await expect(page.getByText(/Postcard sketch.*USD 1\.00/)).toBeVisible();
  await expect(page.getByText(id, { exact: true })).not.toBeAttached();
  const me = await (await page.request.get("http://127.0.0.1:8001/api/auth/me")).json();
  const rejected = await page.request.post(`http://127.0.0.1:8001/api/milestones/${milestoneId}/invoice`, { headers: { "X-CSRF-Token": me.csrf_token }, data: {} });
  expect(rejected.status()).toBe(403);
  expect(errors).toEqual([]);
  await fs.writeFile(`${artifacts}/invoice-demo.json`, JSON.stringify({ contract_id: contractId, invoice_id: id, provider_mode: "mocked_paypal_sandbox", create_requests: creates, mocked_send_requests: sends, client_create_status: rejected.status(), page_errors: errors }, null, 2));
});

test("unsupported offline input has recovery and the selected real fixture produces three outcomes", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/contracts"); await login(page);
  await page.getByRole("button", { name: "Upload agreement", exact: true }).first().click();
  const upload = page.getByRole("dialog");
  const original = "Synthetic unrelated agreement. Only a printed map is included.";
  await upload.getByLabel("Agreement file").setInputFiles({ name: "unsupported.txt", mimeType: "text/plain", buffer: Buffer.from(original) });
  await upload.getByRole("button", { name: "Upload agreement", exact: true }).click();
  await expect(page.getByRole("heading", { name: "unsupported.txt", exact: true })).toBeVisible();
  const originalId = new URL(page.url()).pathname.split("/").pop()!;
  await page.getByRole("tab", { name: "Scope changes", exact: true }).click();
  await page.getByRole("button", { name: "New scope request", exact: true }).first().click();
  const request = page.getByRole("dialog");
  await request.getByLabel("Request title").fill("Unsupported offline work");
  await request.getByLabel("Requested work", { exact: true }).fill("Build a checkout system.");
  await review(request); await request.getByRole("button", { name: "Save client request", exact: true }).click();
  await expect(request).not.toBeVisible();
  await review(page); await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByText("Choose a compatible offline example", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Choose a compatible sample", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Offline demo scenarios", exact: true })).toBeVisible();
  await audit(page, "demo-gallery");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await audit(page, "demo-gallery-mobile");
  await page.screenshot({ path: `${artifacts}/demo-gallery-mobile.jpg`, fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Load Three scope outcomes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Three scope outcomes", exact: true })).toBeVisible();
  await review(page); await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByText("Offline / mocked response", { exact: true })).toBeVisible();
  for (const outcome of ["In scope", "Out of scope", "Ambiguous"]) await expect(page.getByText(outcome, { exact: true })).toBeVisible();
  const unchanged = await page.request.get(`http://127.0.0.1:8001/api/contracts/${originalId}`);
  expect((await unchanged.json()).text).toBe(original);
});
