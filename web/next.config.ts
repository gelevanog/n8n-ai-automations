import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server in .next/standalone for a small Docker image.
  output: "standalone",
  poweredByHeader: false,
  // `next dev` would otherwise write AGENTS.md / CLAUDE.md into this directory.
  agentRules: false,
  devIndicators: false,
};

export default nextConfig;
