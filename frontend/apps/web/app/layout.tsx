import type { Metadata } from 'next'

import { AuthProvider } from '@/providers/auth-provider'

import '@rag-kit/ui/styles/globals.css'

export const metadata: Metadata = {
  title: 'RAG_Kit',
  description:
    'Retrieval-augmented generation over your own documents, with citations.'
}

export default function RootLayout({
  children
}: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
