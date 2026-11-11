'use client'

import { SessionProvider } from 'next-auth/react'

/**
 * SessionProvider needs a client boundary, but the root layout is a server
 * component — this thin wrapper is that boundary.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>
}
