import Link from 'next/link'

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 py-20">
      <div className="mb-12 text-center">
        <span className="section-label mb-4 block">Retrieval-Augmented Generation</span>
        <h1 className="text-h1 gradient-text mb-5">RAG_Kit</h1>
        <p
          className="mx-auto max-w-lg text-body-lg"
          style={{ color: 'var(--color-muted-foreground)' }}
        >
          Upload documents, ask questions, get cited answers from your own knowledge
          base.
        </p>
      </div>

      <div className="mb-10 grid w-full max-w-lg gap-4 sm:grid-cols-2">
        <NavCard
          href="/chat"
          title="Chat"
          description="Ask questions with streamed, cited answers."
          icon={<ChatIcon />}
        />
        <NavCard
          href="/documents"
          title="Documents"
          description="Upload files or ingest URLs and Confluence spaces."
          icon={<DocsIcon />}
        />
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3">
        <Link href="/chat" className="btn-primary">
          Open Chat
        </Link>
        <Link href="/documents" className="btn-secondary">
          Manage Documents
        </Link>
      </div>
    </main>
  )
}

function NavCard({
  href,
  title,
  description,
  icon
}: {
  href: string
  title: string
  description: string
  icon: React.ReactNode
}) {
  return (
    <Link
      href={href}
      className="glass-card flex flex-col gap-3 p-6 transition-all duration-200 hover:border-[var(--color-primary)]/50"
    >
      <div
        className="flex h-10 w-10 items-center justify-center rounded-xl"
        style={{ background: 'rgba(115,223,240,0.1)', color: 'var(--color-primary)' }}
      >
        {icon}
      </div>
      <div>
        <h2 className="text-h3 mb-1">{title}</h2>
        <p className="text-body" style={{ color: 'var(--color-muted-foreground)' }}>
          {description}
        </p>
      </div>
    </Link>
  )
}

function ChatIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  )
}

function DocsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  )
}
