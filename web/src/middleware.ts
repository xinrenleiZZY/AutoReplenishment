import { NextRequest, NextResponse } from "next/server";

/**
 * Phase 0 / G-04：为代理到后端的 /api/* 请求在【服务端】注入鉴权头。
 *
 * - API_AUTH_TOKEN 未配置时不做任何事（与现状一致）；
 * - 配置后，浏览器仍只访问同源 /api/*，token 不会下发到浏览器；
 * - 需与后端 app/config.py 的 API_AUTH_TOKEN 保持一致（两容器同一 .env 即可）。
 */
export function middleware(req: NextRequest) {
  const token = process.env.API_AUTH_TOKEN;
  if (!token) return NextResponse.next();

  const headers = new Headers(req.headers);
  headers.set("x-api-token", token);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/api/:path*"],
};
