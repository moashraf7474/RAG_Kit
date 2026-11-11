import { RegisterForm } from '@/components/forms/register-form'

export const metadata = { title: 'Register · RAG_Kit' }

export default function RegisterPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-h3" style={{ color: 'var(--color-foreground)' }}>Create an account</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--color-muted-foreground)' }}>
          Each account gets its own private knowledge base.
        </p>
      </header>
      <RegisterForm />
    </div>
  )
}
