import { describe, it, expect, vi } from "vitest";
import { apiBase, api, ApiError, request } from "@/lib/api";
import { validateDate, validateMoney } from "@/lib/format";
import { changeRequest, jsonResponse } from "./fixtures";

describe("typed local API boundary", () => {
  it.each([
    ["AI_BUDGET_EXCEEDED", "next UTC midnight"], ["AI_QUOTA_COOLDOWN", "cooling down"],
    ["AI_CONCURRENCY_LIMIT", "AI is busy"], ["AI_INPUT_TOO_LARGE", "never truncated"],
    ["AI_MODELS_UNAVAILABLE", "temporarily unavailable"], ["AI_TIMEOUT", "previous completed result"],
  ])("explains %s and preserves retry guidance", async (code, message) => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: { code, message: "Generic failure" } }), { status: 429, headers: { "Retry-After": "60" } }));
    await expect(request("/contracts")).rejects.toThrow(message);
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: { code } }), { status: 429, headers: { "Retry-After": "60" } }));
    await expect(request("/contracts")).rejects.toThrow("Retry after 60 seconds");
  });
  it("uses public loopback configuration and includes the HttpOnly session cookie", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8001");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ status: "ok" }));
    await request("/health"); expect(apiBase()).toBe("http://localhost:8001");
    expect(fetch).toHaveBeenCalledWith("http://localhost:8001/health", expect.objectContaining({ credentials: "include", cache: "no-store" }));
  });
  it.each(["https://remote.example", "http://user:secret@localhost:8000", "http://127.0.0.1:8000/path", "http://localhost:8000?key=x"])("rejects unsafe public configuration %s", (url) => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", url); expect(apiBase).toThrow(/plain HTTP loopback origin/);
  });
  it("preserves sanitized provider failures without falling back to fixture data", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: { code: "AI_QUOTA_EXCEEDED", message: "Scope analysis failed; the request is preserved" } }, 429));
    await expect(api.analyze(changeRequest, "configured", { reviewer_label: "Reviewer", note: "Compare request" })).rejects.toMatchObject({ status: 429, code: "AI_QUOTA_EXCEEDED" });
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("turns disconnected services into actionable messages", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(request("/contracts")).rejects.toThrow("Cannot reach the local backend");
  });
  it("retains 409 conflicts for the review UI", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Stale revision" }, 409));
    try { await request("/contracts"); throw new Error("Expected failure"); }
    catch (error) { expect(error).toBeInstanceOf(ApiError); expect(error).toMatchObject({ status: 409, message: "Stale revision" }); }
  });
  it.each(["0", "-1", "NaN", "1.001", "1000000000000.01"])("rejects invalid exact money %s", (value) => { expect(validateMoney(value)).not.toBeNull(); });
  it("validates whole JPY and exact decimal bounds without floating point", () => {
    expect(validateMoney("1000000000000.00")).toBeNull(); expect(validateMoney("1.10", "JPY")).not.toBeNull(); expect(validateMoney("0.30")).toBeNull();
  });
  it.each(["2026-02-29", "2026-04-31", "tomorrow", "0000-01-01"])("rejects invalid dates %s", (value) => { expect(validateDate(value)).not.toBeNull(); });
  it.each(["2028-02-29", "2026-12-01"])("keeps explicit valid dates %s", (value) => { expect(validateDate(value)).toBeNull(); });
});
