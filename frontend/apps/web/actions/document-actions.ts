'use server'

import { revalidatePath } from 'next/cache'
import {
  deleteDocument,
  ingestSource,
  reindexDocument,
  uploadDocument
} from '@/lib/rag'

export type ActionResult = { ok: true } | { ok: false; error: string }

function fail(error: unknown): ActionResult {
  return { ok: false, error: error instanceof Error ? error.message : 'Request failed.' }
}

export async function uploadDocumentAction(formData: FormData): Promise<ActionResult> {
  const file = formData.get('file')
  if (!(file instanceof File) || file.size === 0) {
    return { ok: false, error: 'Choose a file to upload.' }
  }
  try {
    await uploadDocument(file)
    revalidatePath('/documents')
    return { ok: true }
  } catch (error) {
    return fail(error)
  }
}

export async function ingestSourceAction(formData: FormData): Promise<ActionResult> {
  const sourceType = String(formData.get('source_type') ?? 'URL') as
    | 'URL'
    | 'SITEMAP'
    | 'CONFLUENCE'
  const sourceUri = String(formData.get('source_uri') ?? '').trim()
  const spaceKey = String(formData.get('space_key') ?? '').trim()

  if (!sourceUri) {
    return { ok: false, error: 'Enter a URL.' }
  }
  try {
    await ingestSource({
      source_type: sourceType,
      source_uri: sourceUri,
      ...(spaceKey ? { space_key: spaceKey } : {})
    })
    revalidatePath('/documents')
    return { ok: true }
  } catch (error) {
    return fail(error)
  }
}

export async function deleteDocumentAction(id: string): Promise<ActionResult> {
  try {
    await deleteDocument(id)
    revalidatePath('/documents')
    return { ok: true }
  } catch (error) {
    return fail(error)
  }
}

export async function reindexDocumentAction(id: string): Promise<ActionResult> {
  try {
    await reindexDocument(id)
    revalidatePath('/documents')
    return { ok: true }
  } catch (error) {
    return fail(error)
  }
}
