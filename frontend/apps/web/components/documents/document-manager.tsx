'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState, useTransition } from 'react'
import {
  deleteDocumentAction,
  ingestSourceAction,
  reindexDocumentAction,
  uploadDocumentAction
} from '@/actions/document-actions'
import type { DocumentStatus, RagDocument } from '@/lib/rag'

const STATUS_COLORS: Record<DocumentStatus, { bg: string; color: string }> = {
  READY:      { bg: 'rgba(115,223,240,0.1)',  color: 'var(--color-primary)' },
  PROCESSING: { bg: 'rgba(251,191,36,0.1)',   color: '#fbbf24' },
  ENRICHING:  { bg: 'rgba(168,85,247,0.1)',    color: '#c084fc' },
  UPLOADING:  { bg: 'rgba(96,165,250,0.1)',   color: '#60a5fa' },
  ERROR:      { bg: 'rgba(239,68,68,0.1)',    color: 'var(--color-destructive)' }
}

export function DocumentManager({ documents }: { documents: RagDocument[] }) {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  const hasPendingWork = documents.some(
    (d) => d.status === 'PROCESSING' || d.status === 'ENRICHING' || d.status === 'UPLOADING'
  )

  useEffect(() => {
    if (!hasPendingWork) return
    const timer = setInterval(() => router.refresh(), 10000)
    return () => clearInterval(timer)
  }, [hasPendingWork, router])

  const run = (action: () => Promise<{ ok: boolean; error?: string }>) => {
    setError(null)
    startTransition(async () => {
      const result = await action()
      if (!result.ok) setError(result.error ?? 'Request failed.')
      else router.refresh()
    })
  }

  return (
    <div className="space-y-5">
      {error && (
        <div
          role="alert"
          className="rounded-2xl px-5 py-4 text-sm"
          style={{
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.2)',
            color: 'var(--color-destructive)'
          }}
        >
          {error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <UploadCard
          disabled={isPending}
          onSubmit={(fd) => run(() => uploadDocumentAction(fd))}
        />
        <SourceCard
          disabled={isPending}
          onSubmit={(fd) => run(() => ingestSourceAction(fd))}
        />
      </div>

      <DocumentTable
        documents={documents}
        disabled={isPending}
        onDelete={(id) => run(() => deleteDocumentAction(id))}
        onReindex={(id) => run(() => reindexDocumentAction(id))}
      />
    </div>
  )
}

/* ─── Upload card ─────────────────────────────────────────────────────────── */
function UploadCard({
  disabled,
  onSubmit
}: {
  disabled: boolean
  onSubmit: (fd: FormData) => void
}) {
  const formRef = useRef<HTMLFormElement>(null)

  return (
    <form
      ref={formRef}
      action={(fd) => { onSubmit(fd); formRef.current?.reset() }}
      className="glass-card p-5 space-y-4"
    >
      <div>
        <h2 className="text-label-sm" style={{ color: 'var(--color-foreground)' }}>
          Upload a file
        </h2>
        <p className="mt-1 text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
          PDF, Word, PowerPoint, Excel, CSV, Markdown, reStructuredText,
          HTML, EPUB or images. Up to 50 MB.
        </p>
      </div>

      <input
        type="file"
        name="file"
        required
        aria-label="File to upload"
        accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.rst,.html,.htm,.xml,.json,.epub,.png,.jpg,.jpeg"
        className="block w-full text-xs"
        style={{ color: 'var(--color-muted-foreground)' }}
      />

      <button type="submit" disabled={disabled} className="btn-primary !py-2 !px-5 !text-sm">
        Upload &amp; index
      </button>
    </form>
  )
}

/* ─── Source card ─────────────────────────────────────────────────────────── */
function SourceCard({
  disabled,
  onSubmit
}: {
  disabled: boolean
  onSubmit: (fd: FormData) => void
}) {
  const [sourceType, setSourceType] = useState<'URL' | 'SITEMAP' | 'CONFLUENCE'>('URL')
  const formRef = useRef<HTMLFormElement>(null)

  const inputStyle = {
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid var(--color-border)',
    borderRadius: '10px',
    color: 'var(--color-foreground)',
    padding: '8px 12px',
    fontSize: '13px',
    width: '100%',
    outline: 'none'
  }

  return (
    <form
      ref={formRef}
      action={(fd) => { onSubmit(fd); formRef.current?.reset() }}
      className="glass-card p-5 space-y-4"
    >
      <div>
        <h2 className="text-label-sm" style={{ color: 'var(--color-foreground)' }}>
          Ingest a source
        </h2>
        <p className="mt-1 text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
          Crawl a web page, a whole sitemap, or a Confluence space.
        </p>
      </div>

      <div className="space-y-2">
        <select
          name="source_type"
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value as typeof sourceType)}
          aria-label="Source type"
          style={inputStyle}
        >
          <option value="URL">Single web page</option>
          <option value="SITEMAP">Sitemap</option>
          <option value="CONFLUENCE">Confluence space</option>
        </select>

        <input
          type="url"
          name="source_uri"
          required
          placeholder="https://example.com/docs"
          aria-label="Source URL"
          style={inputStyle}
        />

        {sourceType === 'CONFLUENCE' && (
          <input
            type="text"
            name="space_key"
            required
            placeholder="Space key (e.g. ENG)"
            aria-label="Confluence space key"
            style={inputStyle}
          />
        )}
      </div>

      <button type="submit" disabled={disabled} className="btn-primary !py-2 !px-5 !text-sm">
        Ingest
      </button>
    </form>
  )
}

/* ─── Document table ──────────────────────────────────────────────────────── */
function DocumentTable({
  documents,
  disabled,
  onDelete,
  onReindex
}: {
  documents: RagDocument[]
  disabled: boolean
  onDelete: (id: string) => void
  onReindex: (id: string) => void
}) {
  if (documents.length === 0) {
    return (
      <div
        className="rounded-3xl p-12 text-center"
        style={{ border: '1px dashed var(--color-border)' }}
      >
        <p className="text-sm" style={{ color: 'var(--color-muted-foreground)' }}>
          No documents yet.
        </p>
        <p className="mt-1 text-xs" style={{ color: 'var(--color-muted-foreground)', opacity: 0.6 }}>
          Upload a file or ingest a URL above to build your knowledge base.
        </p>
      </div>
    )
  }

  return (
    <div
      className="overflow-hidden rounded-3xl"
      style={{ border: '1px solid var(--color-border)', background: 'var(--color-card)' }}
    >
      <table className="w-full text-left text-xs">
        <thead style={{ borderBottom: '1px solid var(--color-border)', background: 'rgba(255,255,255,0.02)' }}>
          <tr>
            {['Name', 'Type', 'Status', 'Chunks', ''].map((col) => (
              <th
                key={col}
                className={`px-4 py-3 font-medium ${col === '' ? 'sr-only' : ''}`}
                style={{ color: 'var(--color-muted-foreground)' }}
              >
                {col || 'Actions'}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {documents.map((doc, i) => (
            <tr
              key={doc.id}
              style={i > 0 ? { borderTop: '1px solid var(--color-border)' } : undefined}
            >
              <td className="max-w-xs px-4 py-3">
                <p className="truncate" style={{ color: 'var(--color-foreground)' }}>
                  {doc.name}
                </p>
                {doc.error_message && (
                  <p
                    className="mt-0.5 truncate text-[11px]"
                    title={doc.error_message}
                    style={{ color: 'var(--color-destructive)' }}
                  >
                    {doc.error_message}
                  </p>
                )}
              </td>
              <td className="px-4 py-3" style={{ color: 'var(--color-muted-foreground)' }}>
                {doc.source_type}
              </td>
              <td className="px-4 py-3">
                <span
                  className="rounded-full px-2.5 py-0.5 text-[11px] font-medium"
                  style={STATUS_COLORS[doc.status]}
                >
                  {doc.status}
                </span>
              </td>
              <td className="px-4 py-3" style={{ color: 'var(--color-muted-foreground)' }}>
                {doc.chunk_count}
              </td>
              <td className="px-4 py-3">
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onReindex(doc.id)}
                    className="rounded-lg px-2.5 py-1 text-[11px] transition disabled:opacity-40"
                    style={{ color: 'var(--color-muted-foreground)', background: 'rgba(255,255,255,0.05)' }}
                  >
                    Re-index
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      if (confirm(`Delete "${doc.name}" and all of its chunks?`)) {
                        onDelete(doc.id)
                      }
                    }}
                    className="rounded-lg px-2.5 py-1 text-[11px] transition disabled:opacity-40"
                    style={{ color: 'var(--color-destructive)', background: 'rgba(239,68,68,0.08)' }}
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
