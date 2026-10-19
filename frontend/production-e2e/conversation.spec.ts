import { test, expect } from "@playwright/test";

test("HTTPS conversation review stays offline, requires freelancer approval and isolates judges", async ({ page, browser }) => {
  await page.goto("/demos");
  await page.getByLabel("Username", { exact: true }).fill("judge.conversation");
  await page.getByLabel("Password", { exact: true }).fill(process.env.SCOPESHIELD_E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const stored = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/change-requests"));
  await page.getByRole("button", { name: "Load B · Gradual scope expansion", exact: true }).click();
  const row = await (await stored).json();
  await expect(page.getByRole("region", { name: "Saved conversation" })).toBeVisible();
  await expect(page.getByRole("option", { name: "Configured AI · disabled in judge demo", exact: true })).toBeDisabled();
  const blocked = await page.evaluate(async (requestId) => {
    const me = await (await fetch("/api/auth/me")).json();
    const response = await fetch(`/api/change-requests/${requestId}/analyze`, { method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token },
      body: JSON.stringify({ expected_revision: 1, mode: "configured", reviewer_label: "Synthetic judge", note: "Verify the public live-provider gate" }) });
    return response.status;
  }, row.id);
  expect(blocked).toBe(403);
  await page.getByLabel("Reviewer name", { exact: true }).fill("Synthetic judge");
  await page.getByLabel("Review note", { exact: true }).fill("Inspect the original clauses and exact message sources");
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByText("Offline / mocked response", { exact: true })).toBeVisible();
  await expect(page.getByText("What changed across the conversation", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare review draft", exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "Evidence Pack", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evidence Pack", exact: true })).toBeVisible();
  // Serialize the extra test read after the UI preview's guarded snapshot work.
  await expect(page.getByRole("button", { name: "Download Evidence Pack PDF", exact: true })).toBeVisible();
  const summary = await page.evaluate(async (id) => (await fetch(`/api/projects/${id}/evidence-pack`)).json(), row.project_id);
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Evidence Pack PDF", exact: true }).click();
  expect(await (await exported).failure()).toBeNull();
  const briefDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download executive summary", exact: true }).click();
  expect(await (await briefDownload).failure()).toBeNull();
  await page.getByRole("tab", { name: "Project overview", exact: true }).click();
  await expect(page.getByRole("region", { name: "Project intelligence", exact: true })).toBeVisible();
  const other = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const second = await other.newPage();
    await second.goto("https://demo.example.test/");
    await second.getByLabel("Username", { exact: true }).fill("judge.two");
    await second.getByLabel("Password", { exact: true }).fill(process.env.SCOPESHIELD_E2E_PASSWORD!);
    await second.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(second.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
    expect(await second.evaluate(async (id) => (await fetch(`/api/change-requests/${id}`)).status, row.id)).toBe(404);
    expect(await second.evaluate(async ({ id, revision }) => (await fetch(`/api/projects/${id}/evidence-pack.pdf?expected_revision=${revision}&executive=true`)).status,
      { id: row.project_id, revision: summary.source_revision })).toBe(404);
  } finally { await other.close(); }
});
