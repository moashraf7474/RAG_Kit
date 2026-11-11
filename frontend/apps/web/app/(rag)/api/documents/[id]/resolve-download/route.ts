/**
 * Fresh signed download-URL proxy.
 *
 * Citations carry a link valid for ~15 minutes, so a URL baked into a stored
 * chat transcript has expired by the time it is clicked. This route attaches
 * the caller's token server-side, asks Django for a freshly signed URL, and
 * returns it so the client can open it immediately.
 */

import { clearToken, DEV_MODE, getApiToken, UnauthenticatedError } from '@/lib/token'

export const dynamic = 'force-dynamic'

const API_URL = process.env.API_URL ?? 'http://api:8000'

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params

  const send = async () =>
    fetch(`${API_URL}/api/documents/${id}/resolve-download/`, {
      headers: { Authorization: `Bearer ${await getApiToken()}` },
      cache: 'no-store'
    })

  let upstream: Response
  try {
    upstream = await send()
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

  if (!upstream.ok) {
    const detail = await upstream.text().catch(() => '')
    return Response.json(
      { error: detail || `Failed to resolve download (${upstream.status})` },
      { status: upstream.status }
    )
  }

  const body = (await upstream.json()) as { download_url?: string }
  if (!body.download_url) {
    return Response.json({ error: 'No downloadable file for this source.' }, { status: 404 })
  }
  return Response.json({ download_url: body.download_url })
}