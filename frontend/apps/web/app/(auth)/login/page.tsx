import { Suspense } from 'react'
import { LoginForm } from '@/components/forms/login-form'

export const metadata = { title: 'Sign in · RAG_Kit' }

export default function LoginPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-h3" style={{ color: 'var(--color-foreground)' }}>Sign in</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--color-muted-foreground)' }}>
          Your documents and conversations are private to your account.
        </p>
      </header>
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </div>
  )
}
