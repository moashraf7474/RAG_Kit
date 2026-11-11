/**
 * NextAuth configuration backed by Django's SimpleJWT endpoints.
 *
 * NextAuth owns only the browser session cookie; Django owns identity. The
 * cookie therefore carries the Django access/refresh pair so server components
 * can call the API as the signed-in user.
 */

import type { NextAuthOptions } from 'next-auth'
import CredentialsProvider from 'next-auth/providers/credentials'

const API_URL = process.env.API_URL ?? 'http://api:8000'

/** Access-token lifetime in ms, read from the JWT itself. */
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

/** The `user_id` SimpleJWT embeds in the access token. */
function decodeUserId(accessToken: string): string {
  try {
    const payload = JSON.parse(
      Buffer.from(accessToken.split('.')[1], 'base64').toString('utf8')
    )
    return String(payload.user_id ?? '')
  } catch {
    return ''
  }
}

export const authOptions: NextAuthOptions = {
  session: { strategy: 'jwt' },
  pages: {
    signIn: '/login'
  },
  providers: [
    CredentialsProvider({
      name: 'Django',
      credentials: {
        username: { label: 'Username', type: 'text' },
        password: { label: 'Password', type: 'password' }
      },
      async authorize(credentials) {
        if (!credentials?.username || !credentials?.password) return null

        const response = await fetch(`${API_URL}/api/token/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: credentials.username,
            password: credentials.password
          }),
          cache: 'no-store'
        })

        // 401 covers both wrong credentials and an account an admin has not
        // activated yet — Django deliberately does not distinguish the two.
        if (!response.ok) return null

        const data = (await response.json()) as { access: string; refresh: string }
        return {
          id: decodeUserId(data.access),
          username: credentials.username,
          accessToken: data.access,
          refreshToken: data.refresh
        }
      }
    })
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        return {
          ...token,
          id: user.id,
          username: user.username,
          accessToken: user.accessToken,
          refreshToken: user.refreshToken,
          accessTokenExpires: decodeExpiry(user.accessToken)
        }
      }

      if (Date.now() < token.accessTokenExpires) return token

      return refreshAccessToken(token)
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken
      session.refreshToken = token.refreshToken
      session.user = { id: token.id, username: token.username }
      if (token.error) session.error = token.error
      return session
    }
  }
}

/**
 * Swap the refresh token for a new access token.
 *
 * On failure the error is carried in the token rather than thrown, so the
 * session resolves and the UI can redirect to /login instead of crashing.
 */
async function refreshAccessToken(
  token: import('next-auth/jwt').JWT
): Promise<import('next-auth/jwt').JWT> {
  try {
    const response = await fetch(`${API_URL}/api/token/refresh/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: token.refreshToken }),
      cache: 'no-store'
    })

    if (!response.ok) throw new Error(`Refresh failed (${response.status})`)

    const data = (await response.json()) as { access: string; refresh?: string }
    return {
      ...token,
      accessToken: data.access,
      // SimpleJWT rotates refresh tokens when ROTATE_REFRESH_TOKENS is on.
      refreshToken: data.refresh ?? token.refreshToken,
      accessTokenExpires: decodeExpiry(data.access),
      error: undefined
    }
  } catch {
    return { ...token, error: 'RefreshFailed' }
  }
}
