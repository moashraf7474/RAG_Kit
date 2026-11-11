/**
 * Typed client for the Django RAG endpoints.
 *
 * These routes are hand-written rather than generated because the streaming
 * chat endpoint returns text/event-stream, which the OpenAPI codegen client
 * cannot express. Everything else still goes through the generated client.
 */

import { clearToken, DEV_MODE, getApiToken } from './token'

export type DocumentStatus = 'UPLOADING' | 'PROCESSING' | 'ENRICHING' | 'READY' | 'ERROR'
export type SourceType = 'FILE' | 'URL' | 'SITEMAP' | 'CONFLUENCE'

export type RagDocument = {
  id: string
  name: string
  source_type: SourceType
  source_uri: string
  status: DocumentStatus
  error_message: string
  content_type: string
  size_bytes: number
  chunk_count: number
  download_url: string
  created_at: string
  modified_at: string
}

export type Citation = {
  page_content: string
  type: 'TEXT' | 'TABLE' | 'IMAGE' | 'SUMMARY'
  document_id: string
  document_name: string
  document_url: string
  page: string
  score: number | null
}

export type ChatAnswer = {
  answer: string
  citations: Citation[]
  finish_reason: string
  conversation_id: string
}

export type Conversation = {
  id: string
  title: string
  message_count: number
  created_at: string
  modified_at: string
}

export type Paginated<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

const API_URL = process.env.API_URL ?? 'http://api:8000'

async function authHeaders(): Promise<Record<string, string>> {
  return { Authorization: `Bearer ${await getApiToken()}` }
}

/**
 * Call the API, retrying once on 401.
 *
 * Only the DEV_MODE token is cached here and so only it can go stale
 * out-of-band (server restart, password change, blacklisted refresh); a
 * one-shot retry turns that from a visible error into a transparent re-login.
 * Real sessions are refreshed by NextAuth, so a 401 there is genuine.
 */
async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = async () =>
    fetch(`${API_URL}${path}`, {
      ...init,
      headers: { ...(await authHeaders()), ...(init.headers ?? {}) },
      cache: 'no-store'
    })

  const response = await send()
  if (response.status !== 401 || !DEV_MODE) return response

  clearToken()
  return send()
}

/** Throws with the API's own message so the UI can surface something useful. */
async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail =
        body.detail ??
        // DRF field errors: {"source_uri": ["..."]}
        Object.entries(body)
          .map(([field, errors]) =>
            Array.isArray(errors) ? `${field}: ${errors.join(', ')}` : `${field}: ${errors}`
          )
          .join('; ') ??
        detail
    } catch {
      // Non-JSON error body; keep the status-code message.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export async function listDocuments(): Promise<RagDocument[]> {
  const response = await apiFetch('/api/documents/?page_size=100')
  const data = await unwrap<Paginated<RagDocument> | RagDocument[]>(response)
  return Array.isArray(data) ? data : data.results
}

export async function uploadDocument(file: File): Promise<RagDocument> {
  const body = new FormData()
  body.append('file', file)
  const response = await apiFetch('/api/documents/upload/', {
    method: 'POST',
    body
  })
  return unwrap<RagDocument>(response)
}

export async function ingestSource(input: {
  source_type: Exclude<SourceType, 'FILE'>
  source_uri: string
  name?: string
  space_key?: string
}): Promise<RagDocument> {
  const response = await apiFetch('/api/documents/source/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input)
  })
  return unwrap<RagDocument>(response)
}

export async function deleteDocument(id: string): Promise<void> {
  const response = await apiFetch(`/api/documents/${id}/`, { method: 'DELETE' })
  if (!response.ok && response.status !== 204) {
    throw new Error(`Could not delete document (${response.status})`)
  }
}

export async function reindexDocument(id: string): Promise<RagDocument> {
  const response = await apiFetch(`/api/documents/${id}/reindex/`, { method: 'POST' })
  return unwrap<RagDocument>(response)
}

export async function listConversations(): Promise<Conversation[]> {
  const response = await apiFetch('/api/chat/conversations/')
  const data = await unwrap<Paginated<Conversation> | Conversation[]>(response)
  return Array.isArray(data) ? data : data.results
}
