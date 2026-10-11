import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type ManualCost, type ManualCostInput, type ManualCostList } from '../api'
import { capitalize, categoryLabel, dayMonth, money, ordinal } from '../format'

/** Settings card: recurring payments that never send a bill email, added by hand to the dashboard's fixed bills. */
export function ManualCosts() {
  const [data, setData] = useState<ManualCostList | null>(null)
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.manualCosts().then(setData, (e: Error) => setError(e.message))
  }, [])

  useEffect(load, [load])

  const done = (changed: boolean) => {
    setEditing(null)
    if (changed) load()
  }

  return (
    <section className="card glass">
      <div className="card-head">
        <h2>Fixed costs without emails</h2>
        {data && editing !== 'new' && (
          <button className="btn" onClick={() => setEditing('new')}>
            Add
          </button>
        )}
      </div>
      {error && <p className="small" style={{ color: 'var(--danger)' }}>{error}</p>}
      {data?.items.map((c) =>
        editing === c.id ? (
          <ManualCostForm key={c.id} cost={c} options={data} onDone={done} />
        ) : (
          <div key={c.id} className="hbar-row">
            <div className="row spread">
              <strong style={{ fontWeight: 600 }}>{c.name}</strong>
              <span className="row" style={{ gap: 6 }}>
                {!c.active && <span className="chip warn">Paused</span>}
                <span className="chip">{categoryLabel(c.category)}</span>
              </span>
            </div>
            <div className="row spread">
              <span className="sub">{describe(c)}</span>
              <button className="btn quiet small" onClick={() => setEditing(c.id)} aria-label={`Edit ${c.name}`}>
                Edit
              </button>
            </div>
          </div>
        ),
      )}
      {data && !data.items.length && editing !== 'new' && (
        <p className="muted small">Nothing added yet.</p>
      )}
      {data && editing === 'new' && <ManualCostForm options={data} onDone={done} />}
      <p className="small muted" style={{ marginBottom: 0 }}>
        For payments that never send a bill email, like a gym membership or a rented water heater. They’re added to
        “Your fixed bills” as a monthly amount. If one starts arriving by email, pause or delete it here so it isn’t
        counted twice.
      </p>
    </section>
  )
}

/** "$120.00 yearly · next Mar 1 · $10.00/mo" */
function describe(c: ManualCost): string {
  const parts = [`${money(c.amount)} ${c.frequency}`]
  if (c.due_day && c.frequency === 'monthly') parts.push(`on the ${ordinal(c.due_day)}`)
  else if (c.next_due_date) parts.push(`next ${dayMonth(c.next_due_date)}`)
  if (c.frequency !== 'monthly') parts.push(`${money(c.monthly)}/mo`)
  return parts.join(' · ')
}

function ManualCostForm({
  cost,
  options,
  onDone,
}: {
  cost?: ManualCost
  options: ManualCostList
  onDone: (changed: boolean) => void
}) {
  const [form, setForm] = useState<ManualCostInput>({
    name: cost?.name ?? '',
    amount: cost?.amount.toFixed(2) ?? '',
    frequency: cost?.frequency ?? 'monthly',
    category: cost?.category ?? 'other',
    due_day: cost?.due_day ?? null,
    next_due_date: cost?.next_due_date ?? null,
    active: cost?.active ?? true,
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (patch: Partial<ManualCostInput>) => setForm((f) => ({ ...f, ...patch }))
  // monthly costs take a day of the month; the rest take their next due date
  const monthly = form.frequency === 'monthly'

  async function run(fn: () => Promise<unknown>) {
    setSaving(true)
    setError('')
    try {
      await fn()
      onDone(true)
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  function save(e: FormEvent) {
    e.preventDefault()
    const body = {
      ...form,
      due_day: monthly ? form.due_day : null,
      next_due_date: monthly ? null : form.next_due_date,
    }
    run(() => (cost ? api.updateManualCost(cost.id, body) : api.addManualCost(body)))
  }

  function remove() {
    if (cost && window.confirm(`Delete ${cost.name}?`)) run(() => api.deleteManualCost(cost.id))
  }

  return (
    <form className="mc-form" onSubmit={save}>
      <div className="mc-fields">
        <label className="mc-wide">
          <span>Name</span>
          <input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="e.g. Anytime Fitness"
            required
          />
        </label>
        <label>
          <span>Amount</span>
          <input
            value={form.amount}
            onChange={(e) => set({ amount: e.target.value })}
            inputMode="decimal"
            placeholder="0.00"
            required
          />
        </label>
        <label>
          <span>How often</span>
          <select value={form.frequency} onChange={(e) => set({ frequency: e.target.value })}>
            {options.frequencies.map((f) => (
              <option key={f} value={f}>
                {capitalize(f)}
              </option>
            ))}
          </select>
        </label>
        {monthly ? (
          <label key="due_day">
            <span>Day of month</span>
            <input
              type="number"
              min={1}
              max={31}
              value={form.due_day ?? ''}
              onChange={(e) => set({ due_day: e.target.value ? Number(e.target.value) : null })}
              placeholder="Optional"
            />
          </label>
        ) : (
          <label key="next_due_date">
            <span>Next due</span>
            <input
              type="date"
              value={form.next_due_date ?? ''}
              onChange={(e) => set({ next_due_date: e.target.value || null })}
            />
          </label>
        )}
        <label>
          <span>Category</span>
          <select value={form.category} onChange={(e) => set({ category: e.target.value })}>
            {options.categories.map((c) => (
              <option key={c} value={c}>
                {categoryLabel(c)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        {cost && (
          <label className="row small muted">
            <input type="checkbox" checked={form.active} onChange={(e) => set({ active: e.target.checked })} />
            Count in fixed bills
          </label>
        )}
        <span style={{ flex: 1 }} />
        {cost && (
          <button type="button" className="btn quiet" style={{ color: 'var(--danger)' }} onClick={remove} disabled={saving}>
            Delete
          </button>
        )}
        <button type="button" className="btn quiet" onClick={() => onDone(false)}>
          Cancel
        </button>
        <button className="btn primary" disabled={saving}>
          {cost ? 'Save' : 'Add'}
        </button>
      </div>
      {error && <div className="small" style={{ color: 'var(--danger)', marginTop: 6 }}>{error}</div>}
    </form>
  )
}
