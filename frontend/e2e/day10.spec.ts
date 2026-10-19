import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS!;
const livePayPal = process.env.SCOPESHIELD_E2E_LIVE === "1";
const liveAI = process.env.SCOPESHIELD_E2E_LIVE_AI === "1";
const backend = "http://127.0.0.1:8001/api";
const source = fileURLToPath(new URL("../../examples/day10_sandbox_agreement.txt", import.meta.url));
async function login(page: Page, username = "qa.freelancer") {
  await page.goto("/");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(process.env.SCOPESHIELD_E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
}
async function review(scope: Page | Locator) {
  await scope.getByLabel("Reviewer name", { exact: true }).fill("Day 10 synthetic reviewer");
  await scope.getByLabel("Review note", { exact: true }).fill("Reviewed original terms and this explicit synthetic action");
}
async function confirm(page: Page, label: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
  const dialog = page.getByRole("dialog"); await review(dialog);
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: `Confirm ${label.toLowerCase()}`, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function picture(page: Page, name: string) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await fs.writeFile(`${artifacts}/axe-day10-${name}.json`, JSON.stringify(result, null, 2));
  expect(result.violations.map(row => row.id)).toEqual([]);
  await page.screenshot({ path: `${artifacts}/day10-${name}.png`, fullPage: false });
}

async function aiPlan() {
  const { createHash } = await import("node:crypto");
  const bytes = await fs.readFile(source);
  return { source_sha256: createHash("sha256").update(bytes).digest("hex"), source_bytes: bytes.length,
    model: "gemini-3.5-flash-lite", max_calls: 1, max_output_tokens: 8192 };
}
async function permission(operation: string, plan: object) {
  await fs.writeFile(`${artifacts}/${operation}-request.json`, JSON.stringify(plan, null, 2), { flag: "wx" });
  await expect.poll(async () => {
    try {
      const decision = JSON.parse(await fs.readFile(`${artifacts}/${operation}-approval.json`, "utf8"));
      if (decision.approved === false) throw new Error("Operator declined the operation");
      return decision.approved === true;
    } catch (error) {
      if (error instanceof Error && error.message === "Operator declined the operation") throw error;
      return false;
    }
  }, { timeout: 15 * 60_000, message: `Waiting for separate human authorization: ${operation}` }).toBe(true);
}

test("fresh complete UI journey, exact money, private clients and durable restart", async ({ page, browser }) => {
  test.skip(process.env.SCOPESHIELD_E2E_DAY10 !== "1", "Requires the runner's isolated restart controller");
  test.setTimeout(livePayPal ? 45 * 60_000 : 180_000);
  page.setDefaultTimeout(20_000);
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  let creates = 0, sends = 0;
  page.on("request", request => {
    if (request.method() === "POST" && /\/milestones\/[^/]+\/invoice$/.test(new URL(request.url()).pathname)) creates++;
    if (request.method() === "POST" && /\/invoice\/send$/.test(new URL(request.url()).pathname)) sends++;
  });
  await login(page);
  await expect(page.getByRole("link", { name: "Try a complete offline walkthrough" })).toBeVisible();
  await picture(page, "overview");
  await page.getByRole("button", { name: "Upload agreement", exact: true }).first().click();
  const upload = page.getByRole("dialog");
  await upload.getByLabel("Agreement file").setInputFiles({ name: "empty.txt", mimeType: "text/plain", buffer: Buffer.alloc(0) });
  await expect(upload.getByRole("alert")).toContainText("non-empty");
  await expect(upload.getByRole("button", { name: "Upload agreement", exact: true })).toBeDisabled();
  await upload.getByLabel("Agreement file").setInputFiles(source);
  await upload.getByRole("button", { name: "Upload agreement", exact: true }).click();
  await expect(page.getByRole("heading", { name: "day10_sandbox_agreement.txt", exact: true })).toBeVisible();
  const contractId = new URL(page.url()).pathname.split("/").pop()!;
  await page.getByRole("button", { name: "Extract agreement", exact: true }).click();
  const extraction = page.getByRole("dialog");
  await expect(extraction).toContainText("Offline demo");
  if (liveAI) {
    await extraction.getByLabel("Analysis provider").selectOption("configured");
    await permission("gemini-extraction", await aiPlan());
  }
  const extracted = page.waitForResponse(response => response.url().endsWith(`/contracts/${contractId}/extract`) && response.request().method() === "POST");
  await extraction.getByRole("button", { name: "Run extraction", exact: true }).click();
  const extractedResponse = await extracted;
  expect(extractedResponse.status()).toBe(200);
  const contract = await extractedResponse.json();
  await expect(page.getByRole("heading", { name: contract.milestones[0].name, exact: true })).toBeVisible();
  await expect(page.locator(".context-provider")).toContainText(liveAI ? "Live provider response" : "Offline / mocked extraction");
  expect(contract.extraction.totals_match).toBe(true);
  expect(contract.extraction.milestone_total).toBe("3.00");
  expect(contract.milestones.map((row: { amount: string }) => row.amount)).toEqual(["1.00", "1.00", "1.00"]);
  expect(contract.milestones.every((row: { status: string }) => row.status === "REVIEW_REQUIRED")).toBe(true);
  await page.getByRole("button", { name: "Locate in agreement", exact: true }).first().click();
  await expect(page.locator("#source-highlight")).not.toBeEmpty();
  await picture(page, "extracted-evidence");
  await page.getByRole("tab", { name: "Invoices", exact: true }).click();
  await expect(page.getByRole("button", { name: "Preview invoice draft", exact: true })).not.toBeAttached();
  const unapproved = await page.request.get(`${backend}/milestones/${contract.milestones[0].id}/invoice/preview`);
  expect(unapproved.status()).toBe(409);
  await page.getByRole("tab", { name: "Milestone review", exact: true }).click();
  await page.getByLabel("Description", { exact: true }).fill("Day 10 reviewed synthetic wireframe deliverable");
  await review(page);
  await page.getByRole("button", { name: "Save changes for review", exact: true }).click();
  await expect(page.getByText("Human edited", { exact: true })).toBeVisible();
  await confirm(page, "Approve milestone");
  await expect(page.getByRole("link", { name: "Review invoice eligibility", exact: true })).toBeVisible();

  const clientContext = await browser.newContext(); const client = await clientContext.newPage();
  await login(client, "qa.client");
  expect((await client.request.get(`${backend}/client/projects/${contract.project_id}`)).status()).toBe(404);
  await page.getByText("Client access to this project", { exact: true }).click();
  await page.getByLabel("Client username", { exact: true }).fill("qa.client");
  await page.getByRole("checkbox", { name: "I reviewed the project contents and intend to share them with this client." }).check();
  await page.getByRole("button", { name: "Share project", exact: true }).click();
  await expect(page.getByText("Project shared with the selected client.", { exact: true })).toBeVisible();
  await client.getByRole("button", { name: "Refresh projects", exact: true }).click();
  await expect(client.getByText(`${contract.milestones[0].name} · USD 1.00`, { exact: true })).toBeVisible();
  await expect(client.getByText(`${contract.milestones[1].name} · USD 1.00`, { exact: true })).not.toBeAttached();
  expect((await client.request.get(`${backend}/contracts/${contractId}`)).status()).toBe(403);
  await picture(client, "client-shared");

  await page.getByRole("tab", { name: "Scope changes", exact: true }).click();
  await page.getByRole("button", { name: "New scope request", exact: true }).first().click();
  const request = page.getByRole("dialog");
  await request.getByRole("button", { name: "Use the three synthetic scenario requests", exact: true }).click();
  await review(request); await request.getByRole("button", { name: "Save client request", exact: true }).click();
  await expect(request).not.toBeVisible();
  await review(page);
  if (liveAI) {
    await page.getByLabel("Analysis provider").selectOption("configured");
    const requests = await (await page.request.get(`${backend}/contracts/${contractId}/change-requests`)).json();
    await permission("gemini-scope", { ...await aiPlan(), requested_changes: requests[0].requested_changes });
  }
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  for (const label of ["In scope", "Out of scope", "Ambiguous"]) await expect(page.getByText(label, { exact: true })).toBeVisible();
  await expect(page.locator(".provenance-strip")).toContainText(liveAI ? "Live provider response" : "Offline / mocked response");
  await page.getByRole("button", { name: "Locate in agreement", exact: true }).nth(1).click();
  await expect(page.locator("#source-highlight")).toHaveText("Online ticket sales, payment processing and shopping cart functionality are excluded from this agreement.");
  await expect(page.locator("#source-highlight")).toBeInViewport();
  await picture(page, "scope-outcomes");
  await confirm(page, "Approve assessment");
  await page.getByLabel("Proposed price (optional)").fill("2.00");
  await page.getByLabel("Proposed deadline (optional)").fill("2026-11-20"); await review(page);
  await page.getByRole("button", { name: "Prepare review draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Draft change order", exact: true })).toBeVisible();
  await page.getByLabel("Proposed price (optional)").fill("2.10");
  await expect(page.getByRole("button", { name: "Approve change order", exact: true })).toBeDisabled();
  await review(page); await page.getByRole("button", { name: "Save draft changes", exact: true }).click();
  await expect(page.getByLabel("Proposed price (optional)")).toHaveValue("2.10");
  await expect(page.getByRole("button", { name: "Save draft changes", exact: true })).toBeDisabled();
  await picture(page, "human-draft-order");

  await page.getByRole("tab", { name: "Invoices", exact: true }).click();
  const previewResponse = page.waitForResponse(response => response.url().endsWith("/invoice/preview") && response.request().method() === "GET");
  await page.getByRole("button", { name: "Preview invoice draft", exact: true }).click();
  const previewData = await (await previewResponse).json();
  const preview = page.getByRole("dialog");
  await expect(preview.getByText("USD 1.00", { exact: true })).toBeVisible();
  if (!livePayPal) await expect(preview.getByText("Mocked PayPal fixture — no real provider action.", { exact: true })).toBeVisible();
  await picture(page, "invoice-preview");
  await review(preview);
  await preview.getByRole("checkbox", { name: "I reviewed the discrepancies and intend this exact partial draft." }).check();
  await preview.getByLabel("Discrepancy explanation").fill("Only the first USD 1 milestone has human approval");
  await preview.getByRole("checkbox", { name: "I confirm creating one sandbox draft for this milestone and recipient." }).check();
  if (livePayPal) await permission("paypal-draft", { amount: previewData.amount, currency: previewData.currency,
    recipient_email: previewData.recipient_email, name: previewData.name, description: previewData.description,
    milestone_id: previewData.milestone_id, contract_id: previewData.contract_id, preview_digest: previewData.preview_digest });
  await preview.getByRole("button", { name: "Create sandbox draft", exact: true }).click();
  await expect(preview).not.toBeVisible();
  const invoice = page.getByRole("region", { name: "Milestone invoice" });
  const invoiceId = await invoice.locator(".record-identifier").innerText();
  expect(invoiceId).toMatch(livePayPal ? /^INV2-(?!MOCK-)/ : /^INV2-MOCK-/);
  await expect(invoice.getByText("PayPal status: DRAFT", { exact: true })).toBeVisible();
  await invoice.getByRole("button", { name: "Refresh PayPal status", exact: true }).click();
  await expect(invoice.getByText("Provider status checked; no new invoice was created.", { exact: true })).toBeVisible();
  await page.getByText("Invoice audit history", { exact: true }).click();
  await picture(page, livePayPal ? "sandbox-draft" : "mocked-draft");
  expect(creates).toBe(1); expect(sends).toBe(0);
  await expect(client.getByText(invoiceId, { exact: true })).not.toBeAttached();
  expect((await client.request.get(`${backend}/milestones/${contract.milestones[0].id}/invoice`)).status()).toBe(403);

  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  const before = await (await page.request.get(`${backend}/contracts/${contractId}/activity`)).json();
  expect(before.some((event: { action: string }) => event.action === "MILESTONE_APPROVED")).toBe(true);
  await fs.writeFile(`${artifacts}/restart-request.json`, JSON.stringify({ contract_id: contractId }));
  await expect.poll(async () => {
    try { return JSON.parse(await fs.readFile(`${artifacts}/restart-complete.json`, "utf8")).restarted; } catch { return false; }
  }, { timeout: 45_000 }).toBe(true);
  await page.reload();
  await expect(page.getByText("Milestone approved", { exact: true })).toBeVisible();
  const after = await (await page.request.get(`${backend}/contracts/${contractId}/activity`)).json();
  expect(after).toEqual(before);
  await picture(page, "audit-after-restart");
  await page.getByRole("tab", { name: "Invoices", exact: true }).click();
  await expect(page.locator(".record-identifier")).toHaveText(invoiceId);
  expect(creates).toBe(1); expect(sends).toBe(0);
  const freshContext = await browser.newContext(); const fresh = await freshContext.newPage();
  await login(fresh); await fresh.goto(`/contracts/${contractId}?tab=invoices`);
  await expect(fresh.locator(".record-identifier")).toHaveText(invoiceId);
  await fresh.getByRole("button", { name: "Sign out", exact: true }).click();
  // Navigation can cancel the asynchronous logout request. Wait for the actual
  // signed-out UI before opening another login, as the other auth workflows do.
  await expect(fresh.getByLabel("Username", { exact: true })).toBeVisible();
  await login(fresh, "qa.other"); await fresh.goto(`/contracts/${contractId}`);
  await expect(fresh.getByRole("alert").filter({ hasText: "Resource not found" })).toBeVisible();
  await freshContext.close(); await clientContext.close();
  expect(errors).toEqual([]);
  await fs.writeFile(`${artifacts}/day10-workflow.json`, JSON.stringify({ contract_id: contractId, project_id: contract.project_id,
    invoice_id: invoiceId, live_ai: liveAI, paypal: livePayPal ? "paypal_sandbox" : "mocked_paypal_sandbox", creates, sends, source: "fresh_day10_synthetic_agreement",
    actual_ui: true, backend_restart: true, fresh_login: true, audit_persisted: true, console_errors: errors }, null, 2));
});
