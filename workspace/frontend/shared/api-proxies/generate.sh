#!/bin/bash
# Generate Next.js API proxy routes for workspace endpoints.
# Usage: bash generate.sh <output_dir> [api_url_env_var]
#
# Example:
#   bash generate.sh src/app/api/workspace
#
# Creates proxy routes that forward /api/workspace/* to the backend.

OUT="${1:-src/app/api/workspace}"
API_VAR="${2:-INTERNAL_API_URL}"

mkdir -p "$OUT" "$OUT/chat/stream" "$OUT/routines/[routineId]/run" "$OUT/routines/[routineId]" "$OUT/config" "$OUT/apps" "$OUT/activity"

HEADER='import { NextRequest, NextResponse } from "next/server"
const API = process.env.'"$API_VAR"' || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002"'

# GET-only routes
for route in config apps activity; do
cat > "$OUT/$route/route.ts" << EOF
$HEADER
export async function GET(req: NextRequest) {
  const url = new URL(req.url)
  const resp = await fetch(\`\${API}/api/workspace/$route\${url.search}\`)
  return NextResponse.json(await resp.json())
}
EOF
done

# Chat (POST)
cat > "$OUT/chat/route.ts" << EOF
$HEADER
export async function POST(req: NextRequest) {
  const body = await req.json()
  const resp = await fetch(\`\${API}/api/workspace/chat\`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })
  return NextResponse.json(await resp.json(), { status: resp.status })
}
EOF

# Chat stream (POST → SSE passthrough)
cat > "$OUT/chat/stream/route.ts" << EOF
$HEADER
export async function POST(req: NextRequest) {
  const body = await req.json()
  const resp = await fetch(\`\${API}/api/workspace/chat/stream\`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) return new Response(JSON.stringify({ error: "API error" }), { status: 502 })
  return new Response(resp.body, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" } })
}
EOF

# Routines (GET + POST)
cat > "$OUT/routines/route.ts" << EOF
$HEADER
export async function GET() {
  const resp = await fetch(\`\${API}/api/workspace/routines\`)
  return NextResponse.json(await resp.json())
}
export async function POST(req: NextRequest) {
  const body = await req.json()
  const resp = await fetch(\`\${API}/api/workspace/routines\`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })
  return NextResponse.json(await resp.json(), { status: resp.status })
}
EOF

# Routine by ID (PATCH + DELETE)
cat > "$OUT/routines/[routineId]/route.ts" << EOF
$HEADER
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ routineId: string }> }) {
  const { routineId } = await params
  const body = await req.json()
  const resp = await fetch(\`\${API}/api/workspace/routines/\${routineId}\`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })
  return NextResponse.json(await resp.json(), { status: resp.status })
}
export async function DELETE(req: NextRequest, { params }: { params: Promise<{ routineId: string }> }) {
  const { routineId } = await params
  const resp = await fetch(\`\${API}/api/workspace/routines/\${routineId}\`, { method: "DELETE" })
  return NextResponse.json(await resp.json(), { status: resp.status })
}
EOF

# Routine run (POST)
cat > "$OUT/routines/[routineId]/run/route.ts" << EOF
$HEADER
export async function POST(req: NextRequest, { params }: { params: Promise<{ routineId: string }> }) {
  const { routineId } = await params
  const resp = await fetch(\`\${API}/api/workspace/routines/\${routineId}/run\`, { method: "POST" })
  return NextResponse.json(await resp.json())
}
EOF

echo "Generated workspace proxy routes in $OUT"
ls -R "$OUT"
