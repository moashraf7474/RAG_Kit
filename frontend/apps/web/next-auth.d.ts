import 'next-auth'
import 'next-auth/jwt'

/**
 * The Django API is the source of truth for identity, so the session carries
 * its JWT pair rather than a NextAuth-owned user record.
 */
declare module 'next-auth' {
  interface Session {
    accessToken: string
    refreshToken: string
    /** Set when the refresh token is also expired; the UI must re-login. */
    error?: 'RefreshFailed'
    user: {
      id: string
      username: string
    }
  }

  interface User {
    id: string
    username: string
    accessToken: string
    refreshToken: string
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    id: string
    username: string
    accessToken: string
    refreshToken: string
    /** Unix ms at which `accessToken` stops being usable. */
    accessTokenExpires: number
    error?: 'RefreshFailed'
  }
}
