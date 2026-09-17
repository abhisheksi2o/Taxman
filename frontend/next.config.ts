import type { NextConfig } from "next";

// The browser only ever talks to this Next.js server; it proxies /api/* to the FastAPI backend so the
// session cookie stays first-party and no backend URL, key or secret is shipped to the client bundle.
const rawBackend = process.env.BACKEND_URL || "http://localhost:8000";
const backend = /^https?:\/\//.test(rawBackend) ? rawBackend.replace(/\/$/, "") : `http://${rawBackend.replace(/\/$/, "")}`;

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
