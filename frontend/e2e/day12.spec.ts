import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

const backend = "http://127.0.0.1:8001/api";
const source = fileURLToPath(new URL("../../examples/day10_sandbox_agreement.txt", import.meta.url));

test("legacy exploit is blocked and retained extraction can retry, approve and preview", async ({ page }) => {
  test.skip(process.env.SCOPESHIELD_E2E_LIVE === "1", "Day 12 verification is strictly offline");
  await page.goto("/");
  await page.getByLabel("Username", { exact: true }).fill("qa.freelancer");
  await page.getByLabel("Password", { exact: true }).fill(process.env.SCOPESHIELD_E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  const me = await (await page.request.get(`${backend}/auth/me`)).json();
  const headers = { "X-CSRF-Token": me.csrf_token };
  for (let i = 0; i < 2; i++) {
    const result = await page.request.post(`${backend}/invoices`, { headers,
      data: { milestone: "Unapproved arbitrary work", amount_usd: "1.00" } });
    expect(result.status()).toBe(410);
    expect((await result.json()).detail.code).toBe("LEGACY_INVOICE_MUTATION_DISABLED");
  }
  await page.getByRole("button", { name: "Upload agreement", exact: true }).first().click();
  await page.getByLabel("Agreement file").setInputFiles(source);
  await page.getByRole("dialog").getByRole("button", { name: "Upload agreement", exact: true }).click();
  await expect(page.getByRole("heading", { name: "day10_sandbox_agreement.txt", exact: true })).toBeVisible();
  const cid = new URL(page.url()).pathname.split("/").pop()!;
  await page.getByRole("button", { name: "Extract agreement", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Run extraction", exact: true }).click();
  await expect(page.getByRole("button", { name: "Extraction saved", exact: true })).toBeVisible();
  const before = await (await page.request.get(`${backend}/contracts/${cid}`)).json();
  async function failRetry() {
    const result = await page.request.post(`${backend}/contracts/${cid}/extract`, { headers, data: { mode: "configured", force: true } });
    expect(result.status()).toBe(503); // The isolated browser backend disables live AI.
    const retained = await (await page.request.get(`${backend}/contracts/${cid}`)).json();
    expect(retained.status).toBe("REVIEW_REQUIRED");
    expect(retained.milestones.every((row: { status: string }) => row.status === "REVIEW_REQUIRED")).toBe(true);
  }
  await failRetry();
  expect((await (await page.request.get(`${backend}/contracts/${cid}`)).json()).milestones).toEqual(before.milestones);
  await page.reload();
  await page.getByRole("button", { name: "Retry extraction", exact: true }).click();
  const retry = page.getByRole("dialog");
  await expect(retry).toContainText("last valid result is preserved");
  await retry.getByRole("button", { name: "Run extraction", exact: true }).click();
  await expect(page.getByRole("button", { name: "Extraction saved", exact: true })).toBeDisabled();
  await failRetry();
  await page.reload();
  await page.getByRole("button", { name: "Approve milestone", exact: true }).click();
  const review = page.getByRole("dialog");
  await review.getByLabel("Reviewer name", { exact: true }).fill("Day 12 synthetic reviewer");
  await review.getByLabel("Review note", { exact: true }).fill("Reviewed the retained original evidence after failed retry");
  await review.getByRole("checkbox").check();
  await review.getByRole("button", { name: "Confirm approve milestone", exact: true }).click();
  await expect(review).not.toBeVisible();
  await page.getByRole("tab", { name: "Invoices", exact: true }).click();
  await page.getByRole("button", { name: "Preview invoice draft", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("USD 1.00");
  await page.getByRole("dialog").getByRole("button", { name: "Back", exact: true }).click();
  const final = await (await page.request.get(`${backend}/contracts/${cid}`)).json();
  expect(final.milestones.filter((row: { status: string }) => row.status === "APPROVED")).toHaveLength(1);
  expect((await page.request.get(`${backend}/milestones/${final.milestones[0].id}/invoice`)).status()).toBe(404);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map(row => row.id)).toEqual([]);
  await fs.writeFile(`${process.env.SCOPESHIELD_E2E_ARTIFACTS}/day12-remediation.json`, JSON.stringify({
    legacy_create_status: 410, failed_retry_status: 503, retained_status: final.status,
    approved_milestones: 1, invoices_created: 0, axe_violations: axe.violations.length,
  }, null, 2));
  await page.screenshot({ path: `${process.env.SCOPESHIELD_E2E_ARTIFACTS}/day12-recovered-invoice-preview.png` });
  // Day 13: real API body admission and stored-document eligibility, no AI/provider call.
  const oversized = await page.request.post(`${backend}/auth/login`, { data: { password: "x".repeat(2 * 1024 * 1024) } });
  expect(oversized.status()).toBe(413);
  const forwarded = await page.request.get(`${backend}/auth/csrf`, { headers: { "X-Forwarded-For": "9.8.7.6" } });
  expect(forwarded.status()).toBe(400);
  const stored = await page.request.post(`${backend}/contracts`, { headers, multipart: {
    file: { name: "day13-large.txt", mimeType: "text/plain", buffer: Buffer.from("x".repeat(25000)) },
  } });
  expect(stored.status()).toBe(201);
  const large = await stored.json();
  expect(large.ai_input_eligible).toBe(false);
  const preflight = await page.request.post(`${backend}/contracts/${large.id}/extract`, { headers, data: { mode: "configured" } });
  expect(preflight.status()).toBe(413);
  await page.goto(`/contracts/${large.id}`);
  await expect(page.getByText(/Stored successfully, but configured extraction exceeds/)).toBeVisible();
  await page.getByRole("button", { name: "Extract agreement", exact: true }).click();
  await page.getByLabel("Analysis provider").selectOption("configured");
  await expect(page.getByRole("button", { name: "Run extraction", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  const day13axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(day13axe.violations).toEqual([]);
  await page.screenshot({ path: `${process.env.SCOPESHIELD_E2E_ARTIFACTS}/day13-input-eligibility.png` });
  await fs.writeFile(`${process.env.SCOPESHIELD_E2E_ARTIFACTS}/day13-hardening.json`, JSON.stringify({
    oversized_json_status: oversized.status(), stored_characters: large.text.length,
    configured_preflight_status: preflight.status(), provider_calls: 0, axe_violations: day13axe.violations.length,
    forwarded_headers_status: forwarded.status(),
  }, null, 2));
});
