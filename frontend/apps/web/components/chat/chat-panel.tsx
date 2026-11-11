'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Citation } from '@/lib/rag'
import { CitationList } from './citation-list'
import { Markdown } from './markdown'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  error?: boolean
}

type StreamEvent =
  | { type: 'citations'; citations: Citation[] }
  | { type: 'token'; token: string }
  | { type: 'done'; finish_reason: string }
  | { type: 'error'; message: string }

export function ChatPanel({
  documentId,
  emptyKb
}: {
  documentId?: string
  emptyKb?: boolean
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const conversationId = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [input])

  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(async () => {
    const q = input.trim()
    if (!q || isStreaming) return

    const uid = crypto.randomUUID()
    const aid = crypto.randomUUID()

    setMessages((p) => [
      ...p,
      { id: uid, role: 'user', content: q, citations: [] },
      { id: aid, role: 'assistant', content: '', citations: [] }
    ])
    setInput('')
    setIsStreaming(true)

    const ctrl = new AbortController()
    abortRef.current = ctrl

    const patch = (u: Partial<ChatMessage>) =>
      setMessages((p) => p.map((m) => (m.id === aid ? { ...m, ...u } : m)))

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          message: q,
          conversation_id: conversationId.current,
          document_id: documentId ?? null
        })
      })

      if (!res.ok || !res.body) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.error ?? 'The assistant is unavailable.')
      }

      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let ans = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const frames = buf.split('\n\n')
        buf = frames.pop() ?? ''
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: StreamEvent
          try { ev = JSON.parse(line.slice(6)) } catch { continue }
          if (ev.type === 'token') { ans += ev.token; patch({ content: ans }) }
          else if (ev.type === 'citations') patch({ citations: ev.citations })
          else if (ev.type === 'error') patch({ content: ev.message, error: true })
        }
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      patch({ content: (e as Error).message || 'Something went wrong.', error: true })
    } finally {
      setIsStreaming(false)
      abortRef.current = null
    }
  }, [input, isStreaming, documentId])

  const reset = () => {
    abortRef.current?.abort()
    conversationId.current = null
    setMessages([])
  }

  const hasMessages = messages.length > 0

  return (
    <div className="flex h-full flex-col" style={{ background: 'var(--color-background)' }}>

      {/* ── Message list ── */}
      <div className="flex-1 overflow-y-auto">
        {!hasMessages ? (
          <EmptyState emptyKb={emptyKb} />
        ) : (
          <div className="mx-auto max-w-3xl px-4 py-8 space-y-8">
            {messages.map((m, i) => (
              <Bubble
                key={m.id}
                msg={m}
                streaming={isStreaming && m.role === 'assistant' && i === messages.length - 1}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* ── Input bar ── */}
      <div
        className="shrink-0 px-4 pb-5 pt-3"
        style={{ borderTop: hasMessages ? '1px solid var(--color-border)' : 'none' }}
      >
        <div className="mx-auto max-w-3xl">

          {/* KB warning */}
          {emptyKb && !hasMessages && (
            <div
              className="mb-3 rounded-xl px-4 py-2.5 text-xs text-center"
              style={{ background: 'rgba(251,191,36,0.07)', border: '1px solid rgba(251,191,36,0.15)', color: '#fbbf24' }}
            >
              No documents are ready yet.{' '}
              <a href="/documents" style={{ color: 'var(--color-primary)', textDecoration: 'underline' }}>
                Upload one first.
              </a>
            </div>
          )}

          {/* Input box */}
          <div
            className="flex items-end gap-3 rounded-2xl px-4 py-3"
            style={{ background: 'var(--color-card)', border: '1px solid var(--color-border)' }}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              rows={1}
              placeholder="Ask anything about your documents…"
              aria-label="Message"
              disabled={isStreaming}
              className="flex-1 resize-none bg-transparent text-sm outline-none"
              style={{
                color: 'var(--color-foreground)',
                minHeight: '24px',
                maxHeight: '200px',
                lineHeight: '1.6',
              }}
            />

            <div className="flex shrink-0 items-center gap-2 pb-0.5">
              {hasMessages && (
                <button
                  type="button"
                  onClick={reset}
                  className="text-xs transition"
                  style={{ color: 'var(--color-muted-foreground)' }}
                  title="Clear chat"
                >
                  <TrashIcon />
                </button>
              )}
              <button
                type="button"
                onClick={() => void send()}
                disabled={!input.trim() || isStreaming}
                className="flex h-8 w-8 items-center justify-center rounded-xl transition"
                style={{
                  background: input.trim() && !isStreaming
                    ? 'var(--color-primary)'
                    : 'rgba(255,255,255,0.07)',
                  color: input.trim() && !isStreaming
                    ? 'var(--color-primary-foreground)'
                    : 'var(--color-muted-foreground)',
                }}
                aria-label="Send"
              >
                {isStreaming ? <StopIcon /> : <SendIcon />}
              </button>
            </div>
          </div>

          <p className="mt-2 text-center text-[11px]" style={{ color: 'var(--color-muted-foreground)', opacity: 0.45 }}>
            Enter to send · Shift+Enter for newline
          </p>
        </div>
      </div>
    </div>
  )
}

/* ─── Empty ─── */
function EmptyState({ emptyKb }: { emptyKb?: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center" style={{ minHeight: '60vh' }}>
      <div
        className="flex h-14 w-14 items-center justify-center rounded-2xl"
        style={{ background: 'rgba(115,223,240,0.08)', color: 'var(--color-primary)' }}
      >
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </div>
      <div className="space-y-1">
        <p className="text-base font-medium" style={{ color: 'var(--color-foreground)' }}>
          What do you want to know?
        </p>
        <p className="max-w-sm text-sm" style={{ color: 'var(--color-muted-foreground)' }}>
          {emptyKb
            ? 'Your knowledge base is empty — upload a document first.'
            : 'Ask anything. Answers are grounded in your documents with source citations.'}
        </p>
      </div>
    </div>
  )
}

/* ─── Bubble ─── */
function Bubble({ msg, streaming }: { msg: ChatMessage; streaming: boolean }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className="max-w-[75%] rounded-2xl rounded-br-sm px-4 py-3 text-sm leading-relaxed"
          style={{ background: 'var(--color-secondary)', color: 'var(--color-foreground)' }}
        >
          <p className="whitespace-pre-wrap">{msg.content}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-3">
      {/* Avatar */}
      <div
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-bold"
        style={{ background: 'linear-gradient(135deg,var(--color-primary),#4db8cc)', color: '#111' }}
      >
        AI
      </div>

      <div className="min-w-0 flex-1 space-y-3 pt-0.5">
        {msg.error ? (
          <p
            className="rounded-xl px-4 py-2.5 text-sm"
            style={{ background: 'rgba(239,68,68,0.08)', color: 'var(--color-destructive)', border: '1px solid rgba(239,68,68,0.15)' }}
          >
            {msg.content}
          </p>
        ) : msg.content ? (
          <div className="text-sm leading-relaxed" style={{ color: 'var(--color-foreground)' }}>
            <Markdown content={msg.content} />
            {streaming && (
              <span
                className="ml-0.5 inline-block h-[1em] w-0.5 animate-pulse rounded-sm align-middle"
                style={{ background: 'var(--color-primary)' }}
              />
            )}
          </div>
        ) : (
          <ThinkingDots />
        )}

        {msg.citations.length > 0 && <CitationList citations={msg.citations} />}
      </div>
    </div>
  )
}

/* ─── Thinking dots ─── */
function ThinkingDots() {
  return (
    <span className="flex items-center gap-1.5" aria-label="Thinking">
      {[0, 160, 320].map((d) => (
        <span
          key={d}
          className="h-1.5 w-1.5 animate-bounce rounded-full"
          style={{ background: 'var(--color-primary)', animationDelay: `${d}ms` }}
        />
      ))}
    </span>
  )
}

/* ─── Icons ─── */
function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
      <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
  )
}
function StopIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
      <rect x="3" y="3" width="18" height="18" rx="3" />
    </svg>
  )
}
function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4h6v2" />
    </svg>
  )
}
