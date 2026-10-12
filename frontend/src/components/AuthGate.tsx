import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { auth, type AuthState } from '../api'

/** Shows the app only once this browser is signed in (launch link) and unlocked with Touch ID. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const check = useCallback(() => {
    auth.status().then((s) => setState(s.state), () => setState('none'))
  }, [])

  useEffect(() => {
    // Passkeys belong to a hostname; 127.0.0.1 can't use them, so always use localhost
    if (window.location.hostname === '127.0.0.1') {
      window.location.replace(window.location.href.replace('//127.0.0.1', '//localhost'))
      return
    }
    check()
    window.addEventListener('auth-required', check)
    return () => window.removeEventListener('auth-required', check)
  }, [check])

  async function ceremony(kind: 'setup' | 'unlock') {
    setBusy(true)
    setError('')
    try {
      if (kind === 'setup') {
        const optionsJSON = await auth.registerOptions()
        await auth.registerVerify(await startRegistration({ optionsJSON }))
      } else {
        const optionsJSON = await auth.unlockOptions()
        await auth.unlockVerify(await startAuthentication({ optionsJSON }))
      }
      setState('unlocked')
    } catch (e) {
      const err = e as Error
      // closing the Touch ID prompt isn't an error worth shouting about
      setError(err.name === 'NotAllowedError' ? 'Touch ID was cancelled. Try again.' : err.message)
      check()
    } finally {
      setBusy(false)
    }
  }

  if (state === null) return null
  if (state === 'unlocked') return <>{children}</>

  const expired = new URLSearchParams(window.location.search).get('login') === 'expired'

  return (
    <div className="page">
      <section className="card glass lock-card">
        <div className="lock-icon" aria-hidden>
          🔒
        </div>
        <h1>Household Bills is locked</h1>

        {state === 'none' && (
          <>
            <p className="muted">
              {expired
                ? 'That sign-in link is from an earlier run of the app. '
                : 'To open it, start the app on this Mac. '}
              It opens a fresh sign-in link in your browser automatically:
            </p>
            <pre className="command">.venv/bin/python -m app</pre>
            <p className="small muted">
              Only someone who can see that link in your terminal can sign in, so other programs on this Mac can't read
              your bank data.
            </p>
          </>
        )}

        {state === 'needs_setup' && (
          <>
            <p className="muted">
              One more step: set up Touch ID. After this, the app opens with your fingerprint and locks itself after 30
              minutes without use.
            </p>
            <button className="btn primary" onClick={() => ceremony('setup')} disabled={busy}>
              {busy && <span className="spinner" aria-hidden />} Set up Touch ID
            </button>
          </>
        )}

        {state === 'locked' && (
          <button className="btn primary" onClick={() => ceremony('unlock')} disabled={busy} autoFocus>
            {busy && <span className="spinner" aria-hidden />} Unlock with Touch ID
          </button>
        )}

        {error && (
          <p className="small" style={{ color: 'var(--danger)' }}>
            {error}
          </p>
        )}
      </section>
    </div>
  )
}
