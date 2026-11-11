/**
 * Streaming chat proxy.
 *
 * Attaches the caller's token — the shared dev account under DEV_MODE,
 * otherwise their own session — and pipes Django's server-sent events straight
 * through to the browser.
 */

import { clearToken, DEV_MODE, getApiToken, UnauthenticatedError } from '@/lib/token'

// Streaming must not be statically optimised or buffered.
export const dynamic = 'force-dynamic'

const API_URL = process.env.API_URL ?? 'http://api:8000'

export async function POST(request: Request) {
  let payload: unknown
  try {
    payload = await request.json()
  } catch {
    return Response.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  const body = JSON.stringify({ ...(payload as object), stream: true })

  const send = async () =>
    fetch(`${API_URL}/api/chat/ask/`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${await getApiToken()}`,
        'Content-Type': 'application/json'
      },
      body
    })

  let upstream: Response
  try {
    upstream = await send()
    // Only the DEV_MODE token is cached here, so only it can be stale.
    if (upstream.status === 401 && DEV_MODE) {
      clearToken()
      upstream = await send()
    }
  } catch (error) {
    if (error instanceof UnauthenticatedError) {
      return Response.json({ error: 'Your session has expired.' }, { status: 401 })
    }
    return Response.json(
      { error: error instanceof Error ? error.message : 'Could not reach the API.' },
      { status: 502 }
    )
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => '')
    return Response.json(
      { error: detail || `Chat request failed (${upstream.status})` },
      { status: upstream.status || 502 }
    )
  }

  return new Response(upstream.body, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      // Prevents proxies from buffering the stream into one big chunk.
      'X-Accel-Buffering': 'no'
    }
  })
}
