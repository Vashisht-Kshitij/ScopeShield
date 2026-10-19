import type { Metadata } from "next";
import { AppShell } from "@/components/shell";
import { AuthBoundary } from "@/components/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ScopeShield — Your work, in scope", template: "%s | ScopeShield" },
  description: "A local workspace to review freelance agreements, compare scope changes, and make evidence-grounded decisions.",
  robots: { index: false, follow: false },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AuthBoundary><AppShell>{children}</AppShell></AuthBoundary></body></html>;
}
