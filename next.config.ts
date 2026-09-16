import type { NextConfig } from "next";

const backend = process.env.PYTHON_API_ORIGIN;

const nextConfig: NextConfig = {
  async rewrites() {
    return backend
      ? [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]
      : [];
  },
};

export default nextConfig;
