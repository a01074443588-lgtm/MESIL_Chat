import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const target = new URL("/", request.url);
  target.searchParams.set("share_error", "install-required");
  return NextResponse.redirect(target, 303);
}
