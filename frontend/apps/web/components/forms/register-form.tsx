'use client'

import Link from 'next/link'
import { useState, useTransition } from 'react'
import { registerAction } from '@/actions/register-action'

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

export function RegisterForm() {
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState(false)
  const [isPending, startTransition] = useTransition()

  const onSubmit = (formData: FormData) => {
    setError(null)
    startTransition(async () => {
      const result = await registerAction(formData)
      if (result.ok) setCreated(true)
      else setError(result.error)
    })
  }

  if (created) {
    return (
      <div className="space-y-4">
        <div
          className="rounded-xl px-4 py-3 text-sm"
          style={{
            background: 'rgba(115,223,240,0.08)',
            border: '1px solid rgba(115,223,240,0.2)',
            color: 'var(--color-primary)'
          }}
        >
          Account created. It still needs to be activated before you can sign in.
        </div>
        <p className="text-xs leading-relaxed" style={{ color: 'var(--color-muted-foreground)' }}>
          New accounts start deactivated. An administrator has to open the Django admin
          at <code style={{ color: 'var(--color-foreground)' }}>/admin/</code>, find
          your user and tick <span style={{ color: 'var(--color-foreground)' }}>Active</span>.
          Running this template locally? You are the administrator — sign in with your
          superuser account and activate it yourself.
        </p>
        <Link
          href="/login"
          className="btn-primary inline-flex !py-2.5 !text-sm"
        >
          Go to sign in
        </Link>
      </div>
    )
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

      <div
        className="rounded-xl px-4 py-3 text-xs leading-relaxed"
        style={{
          background: 'rgba(251,191,36,0.06)',
          border: '1px solid rgba(251,191,36,0.15)',
          color: '#fbbf24'
        }}
      >
        New accounts are created deactivated and must be activated by an administrator
        in the Django admin before they can sign in.
      </div>

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
          minLength={8}
          autoComplete="new-password"
          style={inputStyle}
        />
      </div>

      <div>
        <label
          htmlFor="password_retype"
          className="text-xs font-medium"
          style={{ color: 'var(--color-muted-foreground)' }}
        >
          Repeat password
        </label>
        <input
          id="password_retype"
          name="password_retype"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          style={inputStyle}
        />
      </div>

      <button
        type="submit"
        disabled={isPending}
        className="btn-primary w-full !py-2.5 !text-sm"
      >
        {isPending ? 'Creating account…' : 'Create account'}
      </button>

      <p className="text-center text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
        Already have an account?{' '}
        <Link
          href="/login"
          className="underline"
          style={{ color: 'var(--color-primary)' }}
        >
          Sign in
        </Link>
      </p>
    </form>
  )
}
