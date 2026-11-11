import Link from 'next/link'

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-6 py-16">
      <Link
        href="/"
        className="mb-8 text-sm transition"
        style={{ color: 'var(--color-muted-foreground)' }}
      >
        ← RAG_Kit
      </Link>
      <div className="glass-card w-full max-w-sm p-8">{children}</div>
    </div>
  )
}
