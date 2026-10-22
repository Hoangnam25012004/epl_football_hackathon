import type { NextConfig } from "next";

// Static export served by FastAPI (`pundit serve --ui ../frontend/out`): one origin, no Node
// server in production (docs/day4-design.md section 4.1).
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
};

export default config;
