import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api, type Settings as SettingsData } from '../api'
import { BankAccounts } from '../components/BankAccounts'
import { ManualCosts } from '../components/ManualCosts'
import { OtherAssets } from '../components/OtherAssets'
import { TopBar } from '../components/TopBar'
import { categoryLabel, dateTime, timeAgo } from '../format'

export function Settings() {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<SettingsData | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(params.get('msg') ?? '')
  const [error, setError] = useState(params.get('error') ?? '')
  const [monthsBack, setMonthsBack] = useState(12)
  const [force, setForce] = useState(false)

  const load = useCallback(() => {
    api.settings().then(setData, (e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
    if (params.has('msg') || params.has('error')) setParams({}, { replace: true })
  }, [load]) // eslint-disable-line react-hooks/exhaustive-deps

  async function act(label: string, fn: () => Promise<string>) {
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

  const fetchNow = () =>
    act('fetch', async () => {
      const run = await api.sync()
      if (!run.ok) throw new Error(run.summary)
      return run.summary
    })

  const backfill = () =>
    act('backfill', async () => {
      const run = await api.sync({ months_back: monthsBack, force })
      if (!run.ok) throw new Error(run.summary)
      return run.summary
    })

  const sync = data?.sync
  const last = sync?.last_run

  return (
    <div className="page">
      <TopBar sync={sync} />
      <div className="month-head">
        <h1>Settings</h1>
      </div>
      {msg && <div className="notice glass ok">{msg}</div>}
      {error && <div className="notice glass error">{error}</div>}

      {data && (
        <div className="grid two">
          <div className="stack">
            <section className="card glass">
              <div className="card-head">
                <h2>Gmail</h2>
                {data.gmail_account ? (
                  <span className="chip ok">
                    <span className="status-dot" /> Connected
                  </span>
                ) : (
                  <span className="chip warn">⚠ Not connected</span>
                )}
              </div>
              {data.gmail_account ? (
                <div className="row spread">
                  <span>{data.gmail_account}</span>
                  <button
                    className="btn"
                    onClick={() =>
                      act('disconnect', async () => {
                        await api.disconnectGmail()
                        return 'Gmail disconnected'
                      })
                    }
                  >
                    Disconnect
                  </button>
                </div>
              ) : (
                <a className="btn primary" href="/auth/google/login">
                  Connect Gmail
                </a>
              )}
              <p className="small muted" style={{ marginBottom: 0 }}>
                Read-only access, used only to search the bill senders below. While the Google Cloud app is in
                “Testing” mode, Google ends access every 7 days; reconnect here when that happens.
              </p>
            </section>

            <section className="card glass">
              <div className="card-head">
                <h2>Automatic fetch</h2>
                {sync?.running && (
                  <span className="chip accent">
                    <span className="spinner" aria-hidden /> Fetching
                  </span>
                )}
              </div>
              <dl className="kv">
                <dt>Schedule</dt>
                <dd>Every {sync?.interval_hours} hours while the app is running</dd>
                <dt>Last fetch</dt>
                <dd>
                  {last ? (
                    <>
                      <span className={`chip ${last.ok ? 'ok' : 'danger'}`}>{last.ok ? '✓ OK' : '✕ Failed'}</span>{' '}
                      {timeAgo(last.finished_at)}
                      <span className="sub">{last.summary}</span>
                    </>
                  ) : (
                    'never'
                  )}
                </dd>
                <dt>Next fetch</dt>
                <dd>{data.gmail_account ? dateTime(sync?.next_run_at ?? null) : 'after Gmail is connected'}</dd>
              </dl>
              <div className="row" style={{ marginTop: 16 }}>
                <button className="btn primary" onClick={fetchNow} disabled={!!busy || sync?.running}>
                  {busy === 'fetch' ? <span className="spinner" aria-hidden /> : null} Fetch now
                </button>
              </div>
              <details style={{ marginTop: 16 }}>
                <summary>More options</summary>
                <div className="stack" style={{ marginTop: 12 }}>
                  <div className="row">
                    <span>Fetch the last</span>
                    <input
                      type="number"
                      min={1}
                      max={36}
                      value={monthsBack}
                      onChange={(e) => setMonthsBack(Number(e.target.value))}
                      style={{ width: 70 }}
                    />
                    <span>months</span>
                    <label className="row small muted">
                      <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                      re-download
                    </label>
                    <button className="btn" onClick={backfill} disabled={!!busy || sync?.running}>
                      {busy === 'backfill' ? <span className="spinner" aria-hidden /> : null} Fetch
                    </button>
                  </div>
                  <div className="row">
                    <button
                      className="btn"
                      disabled={!!busy}
                      onClick={() => act('reparse', async () => (await api.reparse()).summary)}
                    >
                      Re-parse stored emails
                    </button>
                    <span className="small muted">after editing parsing rules; no Gmail calls</span>
                  </div>
                </div>
              </details>
            </section>

            <ManualCosts />
          </div>

          <div className="stack">
            <BankAccounts />

            <OtherAssets />

            <section className="card glass">
              <div className="card-head">
                <h2>Bill senders</h2>
                <Link className="btn" to="/settings/senders">
                  Find bill senders
                </Link>
              </div>
              {data.config_error && <p className="small" style={{ color: 'var(--warn)' }}>⚠ {data.config_error}</p>}
              {data.billers.map((b) => (
                <div key={b.name + b.senders.join()} className="hbar-row">
                  <div className="row spread">
                    <strong style={{ fontWeight: 600 }}>{b.name}</strong>
                    <span className="chip">{categoryLabel(b.category)}</span>
                  </div>
                  <span className="sub">
                    {b.senders.join(', ')}
                    {b.subject_includes.length > 0 && <> · subject has “{b.subject_includes.join('” or “')}”</>}
                  </span>
                </div>
              ))}
              <p className="small muted" style={{ marginBottom: 0 }}>
                Advanced rules (custom amount patterns) live in <code>config/bill_senders.toml</code>.
              </p>
            </section>

            <section className="card glass">
              <h2>Recent fetches</h2>
              {sync?.runs.length ? (
                sync.runs.map((r) => (
                  <div key={r.started_at} className="hbar-row">
                    <div className="row spread small">
                      <span>
                        <span className={`status-dot`} style={{ color: r.ok ? 'var(--ok)' : 'var(--danger)' }} />{' '}
                        {dateTime(r.started_at)} · {r.trigger}
                      </span>
                    </div>
                    <span className="sub">{r.summary}</span>
                  </div>
                ))
              ) : (
                <p className="muted small">No fetches yet.</p>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  )
}
