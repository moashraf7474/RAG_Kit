'use client'

import { signOut } from 'next-auth/react'

export function SignOutButton() {
  return (
    <button
      type="button"
      onClick={() => signOut({ callbackUrl: '/login' })}
      className="rounded-lg px-3 py-1.5 text-xs transition"
      style={{
        color: 'var(--color-muted-foreground)',
        background: 'rgba(255,255,255,0.05)',
        border: '1px solid var(--color-border)'
      }}
    >
      Sign out
    </button>
  )
}
