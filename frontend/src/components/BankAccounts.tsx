import { useCallback, useEffect, useState } from 'react'
import { usePlaidLink, type PlaidLinkOnSuccessMetadata } from 'react-plaid-link'
import { api, type BankAccount, type BankConnection, type PlaidStatus } from '../api'
import { dayMonth, money, timeAgo } from '../format'

const TYPE_ORDER = ['depository', 'credit', 'loan', 'investment']
const TYPE_LABELS: Record<string, string> = {
  depository: 'Bank accounts',
  credit: 'Credit cards',
  loan: 'Loans',
  investment: 'Investments',
}

/** What Plaid Link is open for: a new bank, or signing in again to an existing one (update mode). */
type LinkMode = { kind: 'connect' } | { kind: 'reconnect'; item: BankConnection }

/** Settings card: connect TD / RBC / CIBC through Plaid Link and see accounts and balances. */
export function BankAccounts() {
  const [status, setStatus] = useState<PlaidStatus | null>(null)
  const [link, setLink] = useState<{ token: string; mode: LinkMode } | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.plaid().then(setStatus, (e: Error) => setError(e.message))
  }, [])
  useEffect(load, [load])

  async function run(label: string, fn: () => Promise<string>) {
    setBusy(label)
    setMsg('')
    setError('')
    try {
      setMsg(await fn())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
      load()
    }
  }

  const onSuccess = useCallback(
    (publicToken: string | null, metadata: PlaidLinkOnSuccessMetadata) => {
      const mode = link?.mode
      setLink(null)
      if (mode?.kind === 'reconnect') {
        // update mode keeps the same connection: no token exchange, just sync again
        run(`sync-${mode.item.item_id}`, async () => {
          const r = await api.plaidSync(mode.item.item_id)
          return `${mode.item.institution_name} is working again: ${r.added} new transactions.`
        })
        return
      }
      if (!publicToken) {
        setError('Plaid finished without a connection. Try again.')
        return
      }
      run('connect', async () => {
        const name = metadata.institution?.name ?? 'Bank'
        const r = await api.plaidConnect({
          public_token: publicToken,
          institution_name: metadata.institution?.name ?? null,
          institution_id: metadata.institution?.institution_id ?? null,
        })
        return r.added
          ? `Connected ${name}: ${r.accounts} accounts, ${r.added} transactions.`
          : `Connected ${name}: ${r.accounts} accounts. The transaction history can take a few minutes; it syncs automatically, or press Sync.`
      })
    },
    [link], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const { open, ready } = usePlaidLink({
    token: link?.token ?? null,
    onSuccess,
    onExit: (err) => {
      setLink(null)
      if (err) setError(err.display_message || err.error_message)
    },
  })

  // Open Link as soon as a fresh token is ready
  useEffect(() => {
    if (link && ready) open()
  }, [link, ready, open])

  async function startLink(mode: LinkMode) {
    setError('')
    setMsg('')
    try {
      const { link_token } =
        mode.kind === 'connect' ? await api.plaidLinkToken() : await api.plaidReconnectToken(mode.item.item_id)
      setLink({ token: link_token, mode })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const sandbox = status?.environment === 'sandbox'

  return (
    <section className="card glass">
      <div className="card-head">
        <h2>Bank accounts</h2>
        {status &&
          (sandbox ? (
            <span className="chip accent" title="Plaid Sandbox: test banks and made-up data">
              Sandbox · test data
            </span>
          ) : (
            <span className="chip ok" title="Connected to your real banks through Plaid">
              Live
            </span>
          ))}
      </div>

      {status?.problem ? (
        <p className="small" style={{ color: 'var(--warn)' }}>
          ⚠ {status.problem}
        </p>
      ) : (
        <>
          <div className="row">
            <button className="btn primary" onClick={() => startLink({ kind: 'connect' })} disabled={!!busy || !!link}>
              {busy === 'connect' && <span className="spinner" aria-hidden />} Connect a bank
            </button>
          </div>
          {sandbox ? (
            <p className="small muted" style={{ marginBottom: 0 }}>
              Sandbox: pick any bank in the Plaid window and sign in with Plaid's test login{' '}
              <code>user_good</code> / <code>pass_good</code>. Nothing real is connected.
            </p>
          ) : (
            <p className="small muted" style={{ marginBottom: 0 }}>
              🔒 Read-only: this app can see balances and transactions but can't move money. You sign in to your bank in
              Plaid's window; your password never reaches this app.
            </p>
          )}
        </>
      )}

      {msg && <p className="small" style={{ color: 'var(--ok)' }}>{msg}</p>}
      {error && <p className="small" style={{ color: 'var(--danger)' }}>{error}</p>}

      {status?.items.map((item) => (
        <Connection
          key={item.item_id}
          item={item}
          busy={busy || (link ? 'link' : '')}
          onReconnect={() => startLink({ kind: 'reconnect', item })}
          onSync={() =>
            run(`sync-${item.item_id}`, async () => {
              const r = await api.plaidSync(item.item_id)
              return `${item.institution_name}: ${r.added} new, ${r.modified} updated, ${r.removed} removed transactions.`
            })
          }
          onRemove={() =>
            run(`remove-${item.item_id}`, async () => {
              await api.plaidRemove(item.item_id)
              return `Disconnected ${item.institution_name}. Plaid no longer has access.`
            })
          }
        />
      ))}
    </section>
  )
}

function Connection(props: {
  item: BankConnection
  busy: string
  onReconnect: () => void
  onSync: () => void
  onRemove: () => void
}) {
  const { item, busy, onReconnect, onSync, onRemove } = props
  const [confirming, setConfirming] = useState(false)
  const groups = TYPE_ORDER.map((type) => [type, item.accounts.filter((a) => a.type === type)] as const).filter(
    ([, accounts]) => accounts.length,
  )

  return (
    <div className="bank">
      <div className="row spread">
        <div>
          <strong style={{ fontWeight: 600 }}>{item.institution_name}</strong>
          <span className="sub">
            {item.accounts.length} accounts · {item.transaction_count} transactions · synced {timeAgo(item.last_synced_at)}
          </span>
          {item.needs_reconnect ? (
            <span className="sub" style={{ color: 'var(--warn)' }}>
              ⚠ The bank needs you to sign in again before it can update.
            </span>
          ) : (
            item.last_error && <span className="sub" style={{ color: 'var(--danger)' }}>⚠ {item.last_error}</span>
          )}
        </div>
        <div className="row" style={{ gap: 4 }}>
          {item.needs_reconnect ? (
            <button className="btn small primary" onClick={onReconnect} disabled={!!busy}>
              Reconnect
            </button>
          ) : (
            <button className="btn small" onClick={onSync} disabled={!!busy}>
              {busy === `sync-${item.item_id}` && <span className="spinner" aria-hidden />} Sync
            </button>
          )}
          {confirming ? (
            <>
              <button className="btn small quiet" onClick={() => setConfirming(false)}>
                Keep
              </button>
              <button className="btn small danger" onClick={onRemove} disabled={!!busy}>
                Disconnect
              </button>
            </>
          ) : (
            <button className="btn small quiet" onClick={() => setConfirming(true)}>
              Remove
            </button>
          )}
        </div>
      </div>

      {groups.map(([type, accounts]) => (
        <div key={type} className="bank-group">
          <div className="label">{TYPE_LABELS[type] ?? type}</div>
          {accounts.map((a) => (
            <AccountRow key={a.account_id} account={a} />
          ))}
        </div>
      ))}
    </div>
  )
}

function AccountRow({ account: a }: { account: BankAccount }) {
  // Credit cards and loans show what's owed; bank accounts show what's there
  const owed = a.type === 'credit' || a.type === 'loan'
  return (
    <div className="bank-account">
      <div className="row spread">
        <span>
          {a.name}
          {a.mask && <span className="muted"> ••{a.mask}</span>}
        </span>
        <span className="num">
          {money(a.current_balance)}
          {owed && <span className="small muted"> owed</span>}
        </span>
      </div>
      {a.type === 'credit' && a.payment_due_date && (
        <div className="sub" style={a.is_overdue ? { color: 'var(--danger)' } : undefined}>
          {a.is_overdue && '⚠ Overdue · '}Statement {money(a.statement_balance)} · due {dayMonth(a.payment_due_date)}
          {a.minimum_payment != null && <> · minimum {money(a.minimum_payment)}</>}
        </div>
      )}
    </div>
  )
}
