import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { api, type Bill, type Dashboard as DashboardData } from '../api'
import { BankSpending } from '../components/BankSpending'
import { BillsTable } from '../components/BillsTable'
import { FixedCosts } from '../components/FixedCosts'
import { MonthlyChart } from '../components/MonthlyChart'
import { TopBar } from '../components/TopBar'
import { dayMonth, localDate, money, moneyWhole, monthLong, monthName, paymentLabel, paymentSource } from '../format'

export function Dashboard() {
  const [params, setParams] = useSearchParams()
  const month = params.get('month') ?? undefined
  const [data, setData] = useState<DashboardData | null>(null)
  const [loadedFor, setLoadedFor] = useState<string | undefined | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<number | null>(null)

  const load = useCallback(() => {
    api.dashboard(month).then(
      (d) => {
        setData(d)
        setLoadedFor(month)
      },
      (e: Error) => setError(e.message),
    )
  }, [month])

  useEffect(load, [load])

  // pick up results of the background fetch without a manual refresh
  useEffect(() => {
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [load])

  const go = (m: string) => setParams(m === data?.current_month ? {} : { month: m })

  // "Set it" on the next payment opens that bill's edit row in the list below
  const editBill = (id: number) => {
    setEditing(id)
    requestAnimationFrame(() => document.getElementById(`bill-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }

  return (
    <div className="page">
      <TopBar sync={data?.sync} />
      {error && <div className="notice glass error">{error}</div>}
      {data && (
        <main style={{ opacity: loadedFor === month ? 1 : 0.6, transition: 'opacity 0.15s' }}>
          <MonthHero data={data} go={go} editBill={editBill} />

          <section className="card glass" style={{ marginTop: 16 }}>
            <div className="card-head">
              <h2>{monthName(data.month)} bills</h2>
              {data.needs_review > 0 && (
                <span className="chip warn">
                  ⚠ {data.needs_review} {data.needs_review === 1 ? 'needs' : 'need'} an amount
                </span>
              )}
            </div>
            <BillsTable bills={data.bills} editing={editing} setEditing={setEditing} onChanged={load} />
            {data.statements.count > 0 && (
              <p className="small muted" style={{ margin: '12px 0 0' }}>
                + {data.statements.count} card statement{data.statements.count > 1 ? 's' : ''} (
                {money(data.statements.total)}) not counted: that spending comes from the bank.
              </p>
            )}
          </section>

          {data.bank && <BankSpending bank={data.bank} month={data.month} />}

          <section className="grid two">
            <div className="card glass">
              <h2>Bills by month</h2>
              <MonthlyChart history={data.history} selected={data.month} onSelect={go} />
            </div>
            <FixedCosts fixed={data.fixed} />
          </section>
        </main>
      )}
    </div>
  )
}

/** The headline: how much went out on bills in the selected month, and what's still coming. */
function MonthHero({ data, go, editBill }: { data: DashboardData; go: (m: string) => void; editBill: (id: number) => void }) {
  const isCurrent = data.month === data.current_month
  const billsPaid = data.total - data.remaining
  // With real bank data the headline is everything spent; with none (or Sandbox test data) it's the bills
  const bank = data.bank && !data.bank.is_test_data ? data.bank : null
  const [dollars, cents] = money(bank ? bank.total : billsPaid).split('.')
  // a month's bills only come from email, so compare with the emailed part of the fixed bills
  const usual = data.fixed?.items.filter((i) => i.source === 'email').reduce((sum, i) => sum + i.monthly, 0)
  const diff = usual ? data.total - usual : null
  const daysUntil = data.next_bill?.due_date
    ? Math.round((localDate(data.next_bill.due_date).getTime() - localDate(data.today).getTime()) / 86_400_000)
    : null

  return (
    <section className="hero-panel glass month-hero" aria-label={`Spending in ${monthLong(data.month)}`}>
      <div className="hero-main">
        <div className="row spread">
          <div className="eyebrow">
            {bank ? 'Spent' : 'Spent on bills'} · {monthLong(data.month)}
          </div>
          <div className="row" style={{ gap: 6 }}>
            {!isCurrent && (
              <button className="btn small" onClick={() => go(data.current_month)}>
                This month
              </button>
            )}
            <button className="btn icon" aria-label="Previous month" onClick={() => go(data.prev_month)}>
              ‹
            </button>
            <button
              className="btn icon"
              aria-label="Next month"
              disabled={!data.next_month}
              onClick={() => data.next_month && go(data.next_month)}
            >
              ›
            </button>
          </div>
        </div>
        <div className="hero-figure">
          {dollars}
          <span className="cents">.{cents}</span>
        </div>
        <div className="hero-sub">
          {isCurrent ? 'so far this month' : `in ${monthName(data.month)}`}
          {bank
            ? ' · all accounts and cards'
            : data.total > 0 && data.remaining > 0 && <> · of {money(data.total)} due</>}
        </div>
        {!bank && data.total > 0 && (
          <div className="progress hero-progress" title="Bills whose due date has passed" aria-hidden>
            <div style={{ width: `${(billsPaid / data.total) * 100}%` }} />
          </div>
        )}
        <p className="small muted hero-note">
          {bank ? (
            <>
              Includes {money(billsPaid)} of bills so far. Transfers between your accounts and credit card payments
              aren't counted twice.
            </>
          ) : (
            <>
              {data.count} {data.count === 1 ? 'bill' : 'bills'} found in Gmail.{' '}
              {data.bank
                ? 'The bank connection is in Sandbox (test data), so it isn’t counted here yet.'
                : 'Card and bank spending will be added once a bank is connected in Settings.'}
            </>
          )}
        </p>
      </div>

      <div className="hero-side">
        <div>
          <div className="label">Still to come</div>
          <div className="tile-value">{data.remaining > 0 ? money(data.remaining) : 'Nothing'}</div>
          <div className="small muted">
            {data.next_bill
              ? `Next: ${data.next_bill.biller}, ${dayMonth(data.next_bill.due_date)}${
                  daysUntil === 0 ? ' (today)' : daysUntil === 1 ? ' (tomorrow)' : daysUntil ? ` (in ${daysUntil} days)` : ''
                } · ${money(data.next_bill.amount)}`
              : isCurrent
                ? 'No more bills due this month'
                : 'Every bill in this month is past its due date'}
          </div>
          {data.next_bill && <PaymentLine bill={data.next_bill} editBill={editBill} />}
        </div>

        {diff !== null && usual && data.total > 0 && (
          <div className="hero-compare">
            <div className="label">Compared with a usual month</div>
            <div className="small">
              {monthName(data.month)} bills total {money(data.total)}:{' '}
              <strong>
                {diff <= 0 ? '▼' : '▲'} {moneyWhole(Math.abs(diff))} {diff <= 0 ? 'less' : 'more'}
              </strong>{' '}
              than these bills usually come to ({moneyWhole(usual)} a month)
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

/** How the next payment will be made. "You pay this yourself" stands out: it needs action. */
function PaymentLine({ bill, editBill }: { bill: Bill; editBill: (id: number) => void }) {
  const p = bill.payment
  const icon = !p ? '?' : p.method === 'card_auto' ? '💳' : p.method === 'manual' ? '✋' : '🏦'
  return (
    <div className="payment-line">
      <span className={`chip ${!p ? '' : p.method === 'manual' ? 'warn' : 'accent'}`} title={paymentSource(p)}>
        <span aria-hidden>{icon}</span> {paymentLabel(p)}
      </span>
      {!p && (
        <button className="btn quiet small" onClick={() => editBill(bill.id)}>
          Set it
        </button>
      )}
    </div>
  )
}
