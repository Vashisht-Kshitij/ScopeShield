import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { setCsrfToken } from "@/lib/api";

export const routerPush = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => "/contracts", useRouter: () => ({ push: routerPush }) }));
beforeEach(() => { setCsrfToken(""); vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Tests must mock every API request"))); });
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); routerPush.mockReset(); });
