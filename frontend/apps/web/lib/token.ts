/**
 * DEV_MODE: server-side auto-login as a fixed dev user.
 *
 * The Django API always requires a JWT and scopes every document to an owner.
 * With DEV_MODE on, the server signs in as one shared account so `docker
 * compose up` is usable with zero clicks. With it off — the default — every
 * visitor logs in themselves and gets their own documents (see `lib/auth.ts`).
 *
 * This module is server-only: the credentials must never reach the browser.
 */

import 'server-only'

const API_URL = process.env.API_URL ?? 'http://api:8000'
const USERNAME = process.env.DEV_USERNAME ?? 'ragtester'
const PASSWORD = process.env.DEV_PASSWORD ?? ''

/** Whether the shared dev account is standing in for real sessions. */
export const DEV_MODE = process.env.DEV_MODE === 'true'

/**
 * A shared account in production would let any visitor read every other
 * visitor's documents, so refuse to serve rather than fail open.
 *
 * Checked when a token is actually requested rather than at module load:
 * `next build` runs with NODE_ENV=production, and a load-time throw would make
 * a DEV_MODE image impossible to build even though nothing is being served yet.
 */
export function assertDevModeAllowed(): void {
  if (DEV_MODE && process.env.NODE_ENV === 'production') {
    throw new Error(
      'DEV_MODE=true is refused when NODE_ENV=production. DEV_MODE signs every ' +
        'visitor in as the single DEV_USERNAME account, which would expose all ' +
        'documents to everyone. Unset DEV_MODE to require real per-user login.'
    )
  }
}

type CachedToken = { access: string; refresh: string; expiresAt: number }

// Module-scoped cache: one login per server process, not one per request.
let cached: CachedToken | null = null
let inFlight: Promise<CachedToken> | null = null

function decodeExpiry(accessToken: string): number {
  try {
    const payload = JSON.parse(
      Buffer.from(accessToken.split('.')[1], 'base64').toString('utf8')
    )
    // Refresh a minute early so a token cannot expire mid-request.
    return payload.exp * 1000 - 60_000
  } catch {
    return Date.now() + 4 * 60_000
  }
}

async function login(): Promise<CachedToken> {
  assertDevModeAllowed()

  if (!PASSWORD) {
    throw new Error(
      'DEV_MODE=true but DEV_PASSWORD is not set in .env.frontend. Create the ' +
        'dev user with `make superuser` and set DEV_USERNAME / DEV_PASSWORD to match.'
    )
  }

  const response = await fetch(`${API_URL}/api/token/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
    cache: 'no-store'
  })

  if (!response.ok) {
    throw new Error(
      `Dev login failed as "${USERNAME}" (${response.status}). ` +
        'Check DEV_USERNAME / DEV_PASSWORD in .env.frontend.'
    )
  }

  const data = (await response.json()) as { access: string; refresh: string }
  return {
    access: data.access,
    refresh: data.refresh,
    expiresAt: decodeExpiry(data.access)
  }
}

async function refresh(token: CachedToken): Promise<CachedToken> {
  const response = await fetch(`${API_URL}/api/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: token.refresh }),
    cache: 'no-store'
  })

  // Refresh tokens rotate and can be blacklisted; fall back to a fresh login.
  if (!response.ok) return login()

  const data = (await response.json()) as { access: string; refresh?: string }
  return {
    access: data.access,
    refresh: data.refresh ?? token.refresh,
    expiresAt: decodeExpiry(data.access)
  }
}

/** A valid dev access token, logging in or refreshing only when needed. */
export async function getAccessToken(): Promise<string> {
  if (cached && Date.now() < cached.expiresAt) {
    return cached.access
  }

  // Collapse concurrent misses into one request so a page with several
  // server components does not fire N logins.
  if (!inFlight) {
    const previous = cached
    inFlight = (previous ? refresh(previous) : login())
      .then((token) => {
        cached = token
        return token
      })
      .finally(() => {
        inFlight = null
      })
  }

  return (await inFlight).access
}

/** Drop the cached token, forcing a fresh login on the next call. */
export function clearToken(): void {
  cached = null
}

/**
 * The access token for the current caller: the shared dev account under
 * DEV_MODE, otherwise the signed-in user's own token.
 *
 * Every API caller goes through here so the two modes can never diverge.
 * Throws when there is no session, which callers turn into a /login redirect.
 */
export async function getApiToken(): Promise<string> {
  if (DEV_MODE) {
    assertDevModeAllowed()
    return getAccessToken()
  }

  // Imported lazily so DEV_MODE never pulls NextAuth into the server bundle.
  const { getServerSession } = await import('next-auth')
  const { authOptions } = await import('./auth')

  const session = await getServerSession(authOptions)
  if (!session?.accessToken || session.error) {
    throw new UnauthenticatedError()
  }
  return session.accessToken
}

/** Signals "no usable session" so callers can redirect instead of erroring. */
export class UnauthenticatedError extends Error {
  constructor() {
    super('Not signed in.')
    this.name = 'UnauthenticatedError'
  }
}
