import { getServerSession } from 'next-auth'
import Link from 'next/link'
import { redirect } from 'next/navigation'

import { authOptions } from '@/lib/auth'
import { assertDevModeAllowed, DEV_MODE } from '@/lib/token'
import { SignOutButton } from '@/components/forms/sign-out-button'

export default async function RagLayout({ children }: { children: React.ReactNode }) {
  assertDevModeAllowed()

  const session = DEV_MODE ? null : await getServerSession(authOptions)
  if (!DEV_MODE && (!session || session.error)) redirect('/login')

  return (
    <div className="flex h-screen flex-col" style={{ background: 'var(--color-background)' }}>
      {/* ── Nav bar ── */}
      <nav
        className="flex shrink-0 items-center gap-1 px-4 py-2"
        style={{
          background: 'rgba(17,17,17,0.9)',
          borderBottom: '1px solid var(--color-border)',
          backdropFilter: 'blur(12px)',
        }}
      >
        <Link
          href="/"
          className="mr-3 text-sm font-semibold tracking-tight"
          style={{ color: 'var(--color-foreground)' }}
        >
          RAG_Kit
        </Link>

        <Link href="/chat" className="nav-link">Chat</Link>
        <Link href="/documents" className="nav-link">Documents</Link>

        <div className="ml-auto flex items-center gap-2">
          {DEV_MODE ? (
            <span
              className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
              style={{ background: 'rgba(251,191,36,0.1)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)' }}
            >
              Dev
            </span>
          ) : (
            <>
              <span className="text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
                {session?.user.username}
              </span>
              <SignOutButton />
            </>
          )}
        </div>
      </nav>

      {/* ── Page ── */}
      <div className="min-h-0 flex-1 overflow-auto">
        {children}
      </div>
    </div>
  )
}
