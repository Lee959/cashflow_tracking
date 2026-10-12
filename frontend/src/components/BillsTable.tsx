import { Fragment, useState } from 'react'
import { api, type Bill, type PaymentMethod } from '../api'
import { categoryLabel, dayMonth, money, paymentShort, paymentSource } from '../format'

interface Props {
  bills: Bill[]
  editing: number | null
  setEditing: (id: number | null) => void
  onChanged: () => void
}

export function BillsTable({ bills, editing, setEditing, onChanged }: Props) {
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
              <tr id={`bill-${b.id}`}>
                <td className="num" style={{ textAlign: 'left', width: 72 }}>
                  {dayMonth(b.due_date)}
                </td>
                <td>
                  {b.biller}
                  <span className="sub">
                    {categoryLabel(b.category)} ·{' '}
                    <span title={paymentSource(b.payment)} className={b.payment?.method === 'manual' ? 'warn-text' : undefined}>
                      {paymentShort(b.payment)}
                    </span>
                  </span>
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
  if (bill.paid)
    return (
      <span className="chip ok" title={`${bill.paid.name} · ${bill.paid.account}`}>
        ✓ {bill.paid.pending ? 'Paying' : 'Paid'} {dayMonth(bill.paid.date)}
      </span>
    )
  if (bill.status === 'statement')
    return <span className="chip" title="The email only says the statement is ready">Statement</span>
  if (bill.status === 'manual') return <span className="chip">Edited</span>
  if (bill.upcoming) return <span className="chip accent">Upcoming</span>
  return null
}

const METHOD_OPTIONS: { value: PaymentMethod; label: string }[] = [
  { value: 'bank_auto', label: 'Auto-debit from a bank account' },
  { value: 'card_auto', label: 'Auto-charged to a card' },
  { value: 'auto', label: 'Automatic (other)' },
  { value: 'manual', label: 'I pay it myself' },
]

function EditRow({ bill, onDone }: { bill: Bill; onDone: (changed: boolean) => void }) {
  const ownPayment = bill.payment?.source === 'you' ? bill.payment : null
  const [amount, setAmount] = useState(bill.amount?.toFixed(2) ?? '')
  const [due, setDue] = useState(bill.due_date ?? '')
  const [ignored, setIgnored] = useState(false)
  const [method, setMethod] = useState<PaymentMethod | ''>(ownPayment?.method ?? '')
  const [detail, setDetail] = useState(ownPayment?.detail ?? '')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const billChanged = amount !== (bill.amount?.toFixed(2) ?? '') || due !== (bill.due_date ?? '') || ignored
  const paymentChanged = method !== (ownPayment?.method ?? '') || detail !== (ownPayment?.detail ?? '')

  async function save() {
    setSaving(true)
    try {
      if (billChanged) await api.updateBill(bill.id, { amount: amount || null, due_date: due || null, ignored })
      if (paymentChanged) await api.setPayment(bill.biller, method || null, method ? detail || null : null)
      onDone(billChanged || paymentChanged)
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  const fromEmail = bill.payment?.source === 'email' ? paymentShort(bill.payment) : 'unknown'

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
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <label className="row small">
            Paid by
            <select value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod | '')}>
              <option value="">As the emails say ({fromEmail})</option>
              {METHOD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {method && (
            <input
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder="e.g. RBC ••321"
              size={14}
              aria-label="Account or card"
            />
          )}
          <span className="small muted">applies to every {bill.biller} bill</span>
          <span style={{ flex: 1 }} />
          <button className="btn quiet" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={saving || !(billChanged || paymentChanged)}>
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
