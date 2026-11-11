'use client'

import { useState } from 'react'
import type { Citation } from '@/lib/rag'

export function CitationList({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false)

  async function openCitation(citation: Citation) {
    const res = await fetch(`/api/documents/${citation.document_id}/resolve-download`)
    if (!res.ok) {
      const { error } = (await res.json().catch(() => ({}))) as { error?: string }
      throw new Error(error ?? 'Could not open this source.')
    }
    const { download_url } = (await res.json()) as { download_url: string }
    window.open(download_url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div
      className="overflow-hidden rounded-xl text-xs"
      style={{ border: '1px solid var(--color-border)', background: 'rgba(255,255,255,0.03)' }}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-3 py-2 transition"
        style={{ color: 'var(--color-muted-foreground)' }}
      >
        <span className="section-label !text-[10px]">
          {citations.length} source{citations.length === 1 ? '' : 's'}
        </span>
        <span aria-hidden className="text-lg leading-none">
          {expanded ? '−' : '+'}
        </span>
      </button>

      {expanded && (
        <ul
          className="space-y-2 p-3"
          style={{ borderTop: '1px solid var(--color-border)' }}
        >
          {citations.map((citation, i) => (
            <li
              key={`${citation.document_id}-${i}`}
              className="rounded-lg p-2.5"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--color-border)' }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-medium" style={{ color: 'var(--color-foreground)' }}>
                    [{i + 1}] {citation.document_name || 'Untitled'}
                  </p>
                  <p className="mt-0.5 text-[10px]" style={{ color: 'var(--color-muted-foreground)' }}>
                    {citation.page && `Page ${citation.page} · `}
                    {citation.type}
                    {typeof citation.score === 'number' && ` · ${citation.score.toFixed(3)}`}
                  </p>
                </div>
                {citation.document_id && (
                  <button
                    type="button"
                    onClick={() => openCitation(citation)}
                    className="shrink-0 text-[10px] underline transition"
                    style={{ color: 'var(--color-primary)' }}
                  >
                    Open
                  </button>
                )}
              </div>
              <p
                className="mt-2 line-clamp-4 whitespace-pre-wrap text-[10px] leading-relaxed"
                style={{ color: 'var(--color-muted-foreground)' }}
              >
                {citation.page_content}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
