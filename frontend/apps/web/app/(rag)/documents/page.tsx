import { DocumentManager } from '@/components/documents/document-manager'
import { listDocuments, type RagDocument } from '@/lib/rag'

export const metadata = { title: 'Documents · RAG_Kit' }

export const dynamic = 'force-dynamic'

export default async function DocumentsPage() {
  let documents: RagDocument[] = []
  let error: string | null = null

  try {
    documents = await listDocuments()
  } catch (caught) {
    error = caught instanceof Error ? caught.message : 'Could not load documents.'
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <header>
        <span className="section-label">Knowledge Base</span>
        <h1 className="text-h2 gradient-text mt-1">Documents</h1>
        <p className="text-body mt-2" style={{ color: 'var(--color-muted-foreground)' }}>
          Everything here is indexed into your private knowledge base.
        </p>
      </header>

      {error ? (
        <div
          className="rounded-2xl px-5 py-4 text-sm"
          role="alert"
          style={{
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.2)',
            color: 'var(--color-destructive)'
          }}
        >
          {error}
        </div>
      ) : (
        <DocumentManager documents={documents} />
      )}
    </div>
  )
}
