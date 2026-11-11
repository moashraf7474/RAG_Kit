'use server'

const API_URL = process.env.API_URL ?? 'http://api:8000'

export type RegisterResult = { ok: true } | { ok: false; error: string }

/**
 * Create a Django account.
 *
 * Registration is proxied through the server so the browser never needs to
 * know the internal API host.
 */
export async function registerAction(formData: FormData): Promise<RegisterResult> {
  const username = String(formData.get('username') ?? '').trim()
  const password = String(formData.get('password') ?? '')
  const passwordRetype = String(formData.get('password_retype') ?? '')

  if (!username) return { ok: false, error: 'Choose a username.' }
  if (password.length < 8) {
    return { ok: false, error: 'Use a password of at least 8 characters.' }
  }
  if (password !== passwordRetype) return { ok: false, error: 'Passwords do not match.' }

  let response: Response
  try {
    response = await fetch(`${API_URL}/api/users/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, password_retype: passwordRetype }),
      cache: 'no-store'
    })
  } catch {
    return { ok: false, error: 'Could not reach the API. Is the backend running?' }
  }

  if (response.ok) return { ok: true }

  // DRF field errors: {"username": ["A user with that username already exists."]}
  try {
    const body = (await response.json()) as Record<string, unknown>
    const detail = Object.entries(body)
      .map(([field, errors]) =>
        Array.isArray(errors) ? errors.join(' ') : `${field}: ${String(errors)}`
      )
      .join(' ')
    return { ok: false, error: detail || `Registration failed (${response.status}).` }
  } catch {
    return { ok: false, error: `Registration failed (${response.status}).` }
  }
}
