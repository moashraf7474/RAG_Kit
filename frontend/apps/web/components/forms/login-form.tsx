'use client'

import { signIn } from 'next-auth/react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useState, useTransition } from 'react'

const inputStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  background: 'rgba(255,255,255,0.05)',
  border: '1px solid var(--color-border)',
  borderRadius: '10px',
  color: 'var(--color-foreground)',
  padding: '10px 14px',
  fontSize: '14px',
  outline: 'none',
  marginTop: '6px'
}

export function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  const callbackUrl = searchParams.get('callbackUrl') ?? '/chat'

  const onSubmit = (formData: FormData) => {
    setError(null)
    startTransition(async () => {
      const result = await signIn('credentials', {
        username: String(formData.get('username') ?? ''),
        password: String(formData.get('password') ?? ''),
        redirect: false
      })

      if (!result?.ok) {
        setError('Incorrect username or password — or the account has not been activated yet.')
        return
      }

      router.push(callbackUrl)
      router.refresh()
    })
  }

  return (
    <form action={onSubmit} className="space-y-4">
      {error && (
        <div
          role="alert"
          className="rounded-xl px-4 py-3 text-sm"
          style={{
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.2)',
            color: 'var(--color-destructive)'
          }}
        >
          {error}
        </div>
      )}

      <div>
        <label
          htmlFor="username"
          className="text-xs font-medium"
          style={{ color: 'var(--color-muted-foreground)' }}
        >
          Username
        </label>
        <input
          id="username"
          name="username"
          type="text"
          required
          autoComplete="username"
          style={inputStyle}
        />
      </div>

      <div>
        <label
          htmlFor="password"
          className="text-xs font-medium"
          style={{ color: 'var(--color-muted-foreground)' }}
        >
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          style={inputStyle}
        />
      </div>

      <button
        type="submit"
        disabled={isPending}
        className="btn-primary w-full !py-2.5 !text-sm"
      >
        {isPending ? 'Signing in…' : 'Sign in'}
      </button>

      <p className="text-center text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
        No account?{' '}
        <Link
          href="/register"
          className="underline"
          style={{ color: 'var(--color-primary)' }}
        >
          Register
        </Link>
      </p>
    </form>
  )
}
