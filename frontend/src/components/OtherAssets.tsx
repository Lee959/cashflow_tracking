import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type AssetKind, type ManualAsset, type ManualAssetInput, type ManualAssetList } from '../api'
import { dayMonth, money } from '../format'

/** Settings card: what you own or owe that no bank connection reports, counted in the dashboard's net worth. */
export function OtherAssets() {
  const [data, setData] = useState<ManualAssetList | null>(null)
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.assets().then(setData, (e: Error) => setError(e.message))
  }, [])

  useEffect(load, [load])

  const done = (changed: boolean) => {
    setEditing(null)
    if (changed) load()
  }

  return (
    <section className="card glass">
      <div className="card-head">
        <h2>Other assets &amp; debts</h2>
        {data && editing !== 'new' && (
          <button className="btn" onClick={() => setEditing('new')}>
            Add
          </button>
        )}
      </div>
      {error && <p className="small" style={{ color: 'var(--danger)' }}>{error}</p>}
      {data?.items.map((a) =>
        editing === a.id ? (
          <AssetForm key={a.id} asset={a} options={data} onDone={done} />
        ) : (
          <div key={a.id} className="hbar-row">
            <div className="row spread">
              <strong style={{ fontWeight: 600 }}>{a.name}</strong>
              <span className="chip">{data.labels[a.category] ?? a.category}</span>
            </div>
            <div className="row spread">
              <span className="sub">
                {a.kind === 'debt' ? 'You owe' : 'Worth'} {money(a.value)}
                {a.updated_at && <> · updated {dayMonth(a.updated_at)}</>}
              </span>
              <button className="btn quiet small" onClick={() => setEditing(a.id)} aria-label={`Edit ${a.name}`}>
                Edit
              </button>
            </div>
          </div>
        ),
      )}
      {data && !data.items.length && editing !== 'new' && <p className="muted small">Nothing added yet.</p>}
      {data && editing === 'new' && <AssetForm options={data} onDone={done} />}
      <p className="small muted" style={{ marginBottom: 0 }}>
        For what the connected banks don’t report: your home, a car, a loan or mortgage at another lender. Update a
        value now and then; the net worth line on the dashboard uses the new value from that day on.
      </p>
    </section>
  )
}

function AssetForm({
  asset,
  options,
  onDone,
}: {
  asset?: ManualAsset
  options: ManualAssetList
  onDone: (changed: boolean) => void
}) {
  const [form, setForm] = useState<ManualAssetInput>({
    name: asset?.name ?? '',
    kind: asset?.kind ?? 'asset',
    category: asset?.category ?? options.categories.asset[0],
    value: asset?.value.toFixed(2) ?? '',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (patch: Partial<ManualAssetInput>) => setForm((f) => ({ ...f, ...patch }))

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
    run(() => (asset ? api.updateAsset(asset.id, form) : api.addAsset(form)))
  }

  function remove() {
    if (asset && window.confirm(`Delete ${asset.name}?`)) run(() => api.deleteAsset(asset.id))
  }

  // switching between own and owe offers that side's categories
  const setKind = (kind: AssetKind) => set({ kind, category: options.categories[kind][0] })

  return (
    <form className="mc-form" onSubmit={save}>
      <div className="mc-fields">
        <label className="mc-wide">
          <span>Name</span>
          <input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder={form.kind === 'debt' ? 'e.g. Car loan' : 'e.g. House'}
            required
          />
        </label>
        <label>
          <span>Own or owe</span>
          <select value={form.kind} onChange={(e) => setKind(e.target.value as AssetKind)}>
            <option value="asset">Something I own</option>
            <option value="debt">Something I owe</option>
          </select>
        </label>
        <label>
          <span>Category</span>
          <select value={form.category} onChange={(e) => set({ category: e.target.value })}>
            {options.categories[form.kind].map((c) => (
              <option key={c} value={c}>
                {options.labels[c] ?? c}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{form.kind === 'debt' ? 'Amount owed' : 'Value'}</span>
          <input
            value={form.value}
            onChange={(e) => set({ value: e.target.value })}
            inputMode="decimal"
            placeholder="0.00"
            required
          />
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <span style={{ flex: 1 }} />
        {asset && (
          <button type="button" className="btn quiet" style={{ color: 'var(--danger)' }} onClick={remove} disabled={saving}>
            Delete
          </button>
        )}
        <button type="button" className="btn quiet" onClick={() => onDone(false)}>
          Cancel
        </button>
        <button className="btn primary" disabled={saving}>
          {asset ? 'Save' : 'Add'}
        </button>
      </div>
      {error && <div className="small" style={{ color: 'var(--danger)', marginTop: 6 }}>{error}</div>}
    </form>
  )
}
