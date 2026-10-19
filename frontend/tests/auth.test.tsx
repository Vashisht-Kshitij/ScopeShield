import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { AuthBoundary, LoginScreen } from "@/components/auth";
import { request, setCsrfToken } from "@/lib/api";
import { jsonResponse } from "./fixtures";

const info = { user: { id: "user-1", username: "synthetic.user", display_name: "Synthetic user", role: "freelancer" }, csrf_token: "synthetic-session-csrf" };
describe("session UI", () => {
  it("keeps protected content unmounted until a valid session is returned", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Sign in to continue" }, 401));
    render(<AuthBoundary><h1>Private contract text</h1></AuthBoundary>);
    expect(screen.queryByText("Private contract text")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });
  it("loads the authenticated session before rendering protected content", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(info));
    render(<AuthBoundary><h1>Private contract text</h1></AuthBoundary>);
    expect(await screen.findByText("Private contract text")).toBeInTheDocument();
  });
  it("obtains a bound login CSRF token and submits credentials without browser storage", async () => {
    const signedIn = vi.fn();
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith("/csrf") ? jsonResponse({ csrf_token: "login-csrf" }) : jsonResponse(info));
    render(<LoginScreen onLogin={signedIn} />);
    await userEvent.type(screen.getByLabelText("Username", { exact: true }), "synthetic.user");
    await userEvent.type(screen.getByLabelText("Password", { exact: true }), "Synthetic test input");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(signedIn).toHaveBeenCalledWith(info));
    const init = vi.mocked(fetch).mock.calls[1][1];
    expect(init?.headers).toMatchObject({ "X-CSRF-Token": "login-csrf" });
    expect(init?.credentials).toBe("include");
    expect(screen.getByLabelText("Password", { exact: true })).toHaveValue("");
  });
  it("attaches in-memory CSRF only to mutations", async () => {
    setCsrfToken("bound-test-token"); vi.mocked(fetch).mockResolvedValue(jsonResponse({}));
    await request("/contracts"); await request("/contracts", { method: "POST", body: "{}" });
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).not.toHaveProperty("X-CSRF-Token");
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toHaveProperty("X-CSRF-Token", "bound-test-token");
  });
  it("unmounts private content after a protected endpoint reports session expiry", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(info));
    render(<AuthBoundary><h1>Private contract text</h1></AuthBoundary>);
    await screen.findByText("Private contract text");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Sign in to continue" }, 401));
    await expect(request("/contracts")).rejects.toMatchObject({ status: 401 });
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(screen.queryByText("Private contract text")).not.toBeInTheDocument();
  });
});
