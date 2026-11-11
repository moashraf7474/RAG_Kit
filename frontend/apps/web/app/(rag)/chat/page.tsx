import { ChatPanel } from '@/components/chat/chat-panel'
import { listDocuments } from '@/lib/rag'

export const metadata = { title: 'Chat · RAG_Kit' }
export const dynamic = 'force-dynamic'

export default async function ChatPage() {
  let emptyKb = false
  try {
    const docs = await listDocuments()
    emptyKb = docs.filter((d) => d.status === 'READY' || d.status === 'ENRICHING').length === 0
  } catch { /* non-fatal */ }

  return <ChatPanel emptyKb={emptyKb} />
}
