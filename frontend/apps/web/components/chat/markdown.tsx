'use client'

import { Fragment, type ReactNode } from 'react'

/**
 * Minimal Markdown renderer for assistant answers.
 *
 * Deliberately not `dangerouslySetInnerHTML` and deliberately not a Markdown
 * library: model output is untrusted text, and rendering it as React elements
 * makes HTML/script injection structurally impossible. Covers exactly what the
 * answer prompt asks the model to emit — headings, lists, fenced code, inline
 * code, bold, and links.
 */
export function Markdown({ content }: { content: string }) {
  return <div className="space-y-2">{renderBlocks(content)}</div>
}

function renderBlocks(content: string): ReactNode[] {
  const blocks: ReactNode[] = []
  const lines = content.split('\n')
  let index = 0
  let key = 0

  while (index < lines.length) {
    const line = lines[index]

    // Fenced code block
    if (line.trimStart().startsWith('```')) {
      const language = line.trim().slice(3).trim()
      const body: string[] = []
      index++
      while (index < lines.length && !lines[index].trimStart().startsWith('```')) {
        body.push(lines[index])
        index++
      }
      index++ // consume the closing fence
      blocks.push(
        <pre
          key={key++}
          className="overflow-x-auto rounded-xl p-3 text-[11px] leading-relaxed"
          style={{ background: 'rgba(0,0,0,0.4)', color: '#e2e8f0', border: '1px solid var(--color-border)' }}
        >
          <code data-language={language || undefined}>{body.join('\n')}</code>
        </pre>
      )
      continue
    }

    // Heading
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const sizes = ['text-base', 'text-sm', 'text-sm', 'text-xs']
      blocks.push(
        <p key={key++} className={`${sizes[level - 1]} font-medium`} style={{ color: 'var(--color-foreground)' }}>
          {renderInline(heading[2])}
        </p>
      )
      index++
      continue
    }

    // List (bulleted or numbered)
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const items: string[] = []
      const ordered = /^\s*\d+\.\s+/.test(line)
      while (index < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*([-*+]|\d+\.)\s+/, ''))
        index++
      }
      const ListTag = ordered ? 'ol' : 'ul'
      blocks.push(
        <ListTag
          key={key++}
          className={`ml-5 space-y-1 ${ordered ? 'list-decimal' : 'list-disc'}`}
        >
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item)}</li>
          ))}
        </ListTag>
      )
      continue
    }

    // Blank line
    if (!line.trim()) {
      index++
      continue
    }

    // Paragraph: gather until a blank line or the start of another block.
    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].trimStart().startsWith('```') &&
      !/^#{1,4}\s+/.test(lines[index]) &&
      !/^\s*([-*+]|\d+\.)\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index])
      index++
    }
    blocks.push(
      <p key={key++} className="whitespace-pre-wrap leading-relaxed">
        {renderInline(paragraph.join('\n'))}
      </p>
    )
  }

  return blocks
}

/** Handles `code`, **bold**, and [text](url). */
function renderInline(text: string): ReactNode[] {
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let key = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<Fragment key={key++}>{text.slice(lastIndex, match.index)}</Fragment>)
    }
    const token = match[0]

    if (token.startsWith('`')) {
      nodes.push(
        <code key={key++} className="rounded px-1 py-0.5 text-[11px] font-mono" style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--color-primary)' }}>
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith('**')) {
      nodes.push(
        <strong key={key++} className="font-medium" style={{ color: 'var(--color-foreground)' }}>
          {token.slice(2, -2)}
        </strong>
      )
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (link && /^https?:\/\//i.test(link[2])) {
        nodes.push(
          <a
            key={key++}
            href={link[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
            style={{ color: 'var(--color-primary)' }}
          >
            {link[1]}
          </a>
        )
      } else {
        nodes.push(<Fragment key={key++}>{token}</Fragment>)
      }
    }
    lastIndex = match.index + token.length
  }

  if (lastIndex < text.length) {
    nodes.push(<Fragment key={key++}>{text.slice(lastIndex)}</Fragment>)
  }
  return nodes
}
