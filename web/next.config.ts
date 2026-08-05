import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";

// next.config 加载前读取 .env.local（Next.js 默认不加载）
const projectDir = process.cwd();
loadEnvConfig(projectDir);

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_BASE_URL || "http://127.0.0.1:9101"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
