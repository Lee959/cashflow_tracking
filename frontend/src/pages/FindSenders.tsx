import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type Candidate, type ScanResult } from '../api'
import { TopBar } from '../components/TopBar'
import { categoryLabel, dayMonth, money } from '../format'

type Choice = { picked: boolean; name: string; category: string; subject_filter: string; whole_domain: boolean }

const GROUPS: { key: keyof ScanResult['groups']; title: string; note: string; collapsed?: boolean }[] = [
  {
    key: 'bills',
    title: 'Bills',
    note: 'Services billed every month or every 2 months. Ones whose email states an amount are pre-ticked.',
  },
  {
    key: 'periodic_bills',
    title: 'Other bills: a few times a year, or once',
    note: 'Property tax, annual renewals: real fixed costs even though they aren’t monthly.',
  },
  {
    key: 'card_statements',
    title: 'Credit card statements',
    note: 'Not ticked on purpose: a card statement totals what you bought with the card, so if a bill is charged to the card it would count twice. Card spending will come from the bank.',
  },
  {
    key: 'not_bills',
    title: 'Not bills',
    note: 'Investment statements, purchases and travel, one-off notices.',
    collapsed: true,
  },
]

export function FindSenders() {
  const navigate = useNavigate()
  const [days, setDays] = useState(365)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [choices, setChoices] = useState<Record<string, Choice>>({})
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState('')

  async function scan(refresh = false) {
    setScanning(true)
    setError('')
    try {
      const r = await api.scanSenders(days, refresh)
      setResult(r)
      const initial: Record<string, Choice> = {}
      for (const [key, group] of Object.entries(r.groups))
        for (const c of group)
          initial[c.email] = {
            picked: key === 'bills' && c.recommended && !c.already,
            name: c.name,
            category: c.category,
            subject_filter: c.subject_filter,
            whole_domain: false,
          }
      setChoices(initial)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setScanning(false)
    }
  }

  async function save() {
    const senders = Object.entries(choices)
      .filter(([, c]) => c.picked)
      .map(([email, c]) => ({ email, ...c }))
    try {
      const { added } = await api.addSenders(senders)
      navigate(`/settings?msg=${encodeURIComponent(`Added ${added} bill sender(s). They'll be fetched automatically.`)}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const update = (email: string, patch: Partial<Choice>) =>
    setChoices((prev) => ({ ...prev, [email]: { ...prev[email], ...patch } }))
  const pickedCount = Object.values(choices).filter((c) => c.picked).length

  return (
    <div className="page">
      <TopBar />
      <div className="month-head">
        <h1>Find bill senders</h1>
        <Link className="btn" to="/settings">
          Back to settings
        </Link>
      </div>

      <section className="card glass" style={{ marginTop: 16 }}>
        <div className="row">
          <span>Scan Gmail for bill-like emails from the last</span>
          <input type="number" min={60} max={730} value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ width: 80 }} />
          <span>days</span>
          <button className="btn primary" onClick={() => scan()} disabled={scanning}>
            {scanning && <span className="spinner" aria-hidden />} {scanning ? 'Scanning…' : 'Scan'}
          </button>
          {result && (
            <button className="btn quiet" onClick={() => scan(true)} disabled={scanning}>
              Re-scan
            </button>
          )}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Skips notifications, your own address, people and Re:/Fwd: conversations; groups the rest by sender, works
          out how often each one writes, and opens its latest email to see whether it states an amount. Takes 10–30
          seconds.
        </p>
      </section>

      {error && <div className="notice glass error">{error}</div>}

      {result &&
        GROUPS.map((g) => {
          const table = (
            <SenderTable
              candidates={result.groups[g.key]}
              categories={result.categories}
              choices={choices}
              update={update}
              showKind={g.key === 'not_bills'}
            />
          )
          return g.collapsed ? (
            <details key={g.key} className="card glass" style={{ marginTop: 16 }}>
              <summary>
                {g.title} ({result.groups[g.key].length})
              </summary>
              <p className="small muted">{g.note}</p>
              {table}
            </details>
          ) : (
            <section key={g.key} className="card glass" style={{ marginTop: 16 }}>
              <h2>
                {g.title} ({result.groups[g.key].length})
              </h2>
              <p className="small muted" style={{ marginTop: -6 }}>
                {g.note}
              </p>
              {table}
            </section>
          )
        })}

      {result && (
        <div className="row spread" style={{ marginTop: 16 }}>
          <span className="small muted">
            Skipped {result.skipped.notifications} notifications and {result.skipped.personal} emails from people or
            conversations.
          </span>
          <button className="btn primary" onClick={save} disabled={!pickedCount}>
            {pickedCount ? `Save ${pickedCount} sender${pickedCount === 1 ? '' : 's'}` : 'Tick senders to add'}
          </button>
        </div>
      )}
    </div>
  )
}

function SenderTable(props: {
  candidates: Candidate[]
  categories: string[]
  choices: Record<string, Choice>
  update: (email: string, patch: Partial<Choice>) => void
  showKind: boolean
}) {
  const { candidates, categories, choices, update, showKind } = props
  if (!candidates.length) return <div className="empty">None found.</div>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th />
            <th>Sender</th>
            <th>How often</th>
            <th>Latest email has</th>
            <th>Name in app</th>
            <th>Category</th>
            <th title="Only emails whose subject contains this word count as bills">Subject has</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => {
            const choice = choices[c.email]
            return (
              <tr key={c.email} className={c.already || c.duplicate_of ? 'faded' : undefined}>
                <td>
                  {c.already ? (
                    <span className="chip ok">Added</span>
                  ) : (
                    <input
                      type="checkbox"
                      checked={choice?.picked ?? false}
                      onChange={(e) => update(c.email, { picked: e.target.checked })}
                      aria-label={`Add ${c.name}`}
                    />
                  )}
                </td>
                <td>
                  {c.name}
                  <span className="sub">{c.email}</span>
                  {c.duplicate_of && <span className="sub" style={{ color: 'var(--warn)' }}>Reminder for {c.duplicate_of}: skip</span>}
                  {showKind && <span className="sub">{c.kind_label}</span>}
                  <span className="sub" title={c.latest_subject}>
                    “{c.latest_subject.slice(0, 48)}
                    {c.latest_subject.length > 48 ? '…' : ''}” · {dayMonth(c.latest_at)}
                  </span>
                </td>
                <td>
                  {c.cadence_label}
                  <span className="sub">{c.count} emails</span>
                </td>
                <td>
                  {c.content === 'amount' ? (
                    <span className="chip ok">{money(c.sample_amount)}</span>
                  ) : c.content === 'statement_notice' ? (
                    <span className="chip">Statement notice</span>
                  ) : c.content === 'no_amount' ? (
                    <span className="chip warn">No amount</span>
                  ) : (
                    <span className="small muted">not checked</span>
                  )}
                </td>
                <td>
                  <input value={choice?.name ?? ''} onChange={(e) => update(c.email, { name: e.target.value })} size={14} />
                </td>
                <td>
                  <select value={choice?.category} onChange={(e) => update(c.email, { category: e.target.value })}>
                    {categories.map((cat) => (
                      <option key={cat} value={cat}>
                        {categoryLabel(cat)}
                      </option>
                    ))}
                  </select>
                  <label className="row small muted" style={{ marginTop: 4, gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={choice?.whole_domain ?? false}
                      onChange={(e) => update(c.email, { whole_domain: e.target.checked })}
                    />
                    all @{c.domain}
                  </label>
                </td>
                <td>
                  <input
                    value={choice?.subject_filter ?? ''}
                    onChange={(e) => update(c.email, { subject_filter: e.target.value })}
                    size={8}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
