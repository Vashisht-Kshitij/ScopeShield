import type { NextConfig } from "next";
// NEXT_PUBLIC values are compiled into browser assets; rebuilding is mandatory.
if (process.env.SCOPESHIELD_FRONTEND_PROFILE === "production") {
  const origin = process.env.NEXT_PUBLIC_FRONTEND_URL || "";
  if (!/^https:\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(origin) || process.env.NEXT_PUBLIC_API_BASE_URL !== "same-origin") {
    throw new Error("Production builds require an explicit HTTPS frontend origin and same-origin API routing");
  }
}
const config: NextConfig = {
  env: { NEXT_PUBLIC_SCOPESHIELD_FRONTEND_PROFILE: process.env.SCOPESHIELD_FRONTEND_PROFILE || "local",
         NEXT_PUBLIC_SCOPESHIELD_STUDIO: process.env.NEXT_PUBLIC_SCOPESHIELD_STUDIO || "" },
  poweredByHeader: false,
  devIndicators: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};
export default config;
