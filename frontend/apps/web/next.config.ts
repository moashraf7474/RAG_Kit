import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'standalone',
  transpilePackages: ['@rag-kit/types', '@rag-kit/ui'],
  experimental: {
    serverActions: {
      // Files are uploaded through a server action (FormData), so the default
      // 1 MB request cap silently drops books/large PDFs with a 413. Match the
      // backend's INGESTION_MAX_FILE_SIZE_MB=50.
      bodySizeLimit: '60mb'
    }
  }
}

export default nextConfig
