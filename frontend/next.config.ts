import type { NextConfig } from "next";

// Two deployment shapes share one code base:
//  * server mode  – the browser talks to this Next.js server, which proxies /api/* to the FastAPI backend so the
//                   session cookie stays first-party and no backend URL, key or secret is shipped to the client;
//  * static mode  – STATIC_EXPORT=1 produces a fully static site (GitHub Pages) in which the Python domain code
//                   runs inside the browser via Pyodide behind the same API contract (see src/lib/localBackend.ts).
const isStatic = process.env.STATIC_EXPORT === "1";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
const rawBackend = process.env.BACKEND_URL || "http://localhost:8000";
const backend = /^https?:\/\//.test(rawBackend) ? rawBackend.replace(/\/$/, "") : `http://${rawBackend.replace(/\/$/, "")}`;

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = isStatic
  ? {
      output: "export",
      basePath,
      assetPrefix: basePath || undefined,
      trailingSlash: true,
      reactStrictMode: true,
      poweredByHeader: false,
      images: { unoptimized: true },
    }
  : {
      output: "standalone",
      reactStrictMode: true,
      poweredByHeader: false,
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
      },
      async headers() {
        return [{ source: "/(.*)", headers: securityHeaders }];
      },
    };

export default nextConfig;
