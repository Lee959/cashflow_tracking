import { Fragment, useState } from 'react'
import { api, type Bill } from '../api'
import { categoryLabel, dayMonth, money } from '../format'

export function BillsTable({ bills, onChanged }: { bills: Bill[]; onChanged: () => void }) {
  const [editing, setEditing] = useState<number | null>(null)

  if (!bills.length) return <div className="empty">No bills due this month.</div>

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Due</th>
            <th>Bill</th>
            <th className="num">Amount</th>
            <th />
            <th />
          </tr>
        </thead>
        <tbody>
          {bills.map((b) => (
            <Fragment key={b.id}>
              <tr>
                <td className="num" style={{ textAlign: 'left', width: 72 }}>
                  {dayMonth(b.due_date)}
                </td>
                <td>
                  {b.biller}
                  <span className="sub">{categoryLabel(b.category)}</span>
                </td>
                <td className="num">{money(b.amount)}</td>
                <td>
                  <StatusChip bill={b} />
                </td>
                <td className="num">
                  {b.attachments.map((a) => (
                    <a key={a.id} className="btn quiet small" href={`/attachments/${a.id}`} target="_blank" rel="noreferrer">
                      PDF
                    </a>
                  ))}
                  <button
                    className="btn quiet small"
                    onClick={() => setEditing(editing === b.id ? null : b.id)}
                    aria-expanded={editing === b.id}
                  >
                    Edit
                  </button>
                </td>
              </tr>
              {editing === b.id && (
                <EditRow
                  bill={b}
                  onDone={(changed) => {
                    setEditing(null)
                    if (changed) onChanged()
                  }}
                />
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatusChip({ bill }: { bill: Bill }) {
  if (bill.status === 'needs_review') return <span className="chip warn" title={bill.notes}>⚠ Needs amount</span>
  if (bill.status === 'statement')
    return <span className="chip" title="The email only says the statement is ready">Statement</span>
  if (bill.status === 'manual') return <span className="chip">Edited</span>
  if (bill.upcoming) return <span className="chip accent">Upcoming</span>
  return null
}

function EditRow({ bill, onDone }: { bill: Bill; onDone: (changed: boolean) => void }) {
  const [amount, setAmount] = useState(bill.amount?.toFixed(2) ?? '')
  const [due, setDue] = useState(bill.due_date ?? '')
  const [ignored, setIgnored] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    try {
      await api.updateBill(bill.id, { amount: amount || null, due_date: due || null, ignored })
      onDone(true)
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  return (
    <tr className="edit-row">
      <td colSpan={5}>
        <div className="row">
          <label className="row small">
            Amount
            <input value={amount} onChange={(e) => setAmount(e.target.value)} size={9} inputMode="decimal" />
          </label>
          <label className="row small">
            Due
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label className="row small muted">
            <input type="checkbox" checked={ignored} onChange={(e) => setIgnored(e.target.checked)} />
            Not a bill
          </label>
          <span style={{ flex: 1 }} />
          <button className="btn quiet" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={saving}>
            Save
          </button>
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          {bill.subject}
        </div>
        {error && <div className="small" style={{ color: 'var(--danger)' }}>{error}</div>}
      </td>
    </tr>
  )
}
