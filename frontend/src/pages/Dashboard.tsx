import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { api, type Dashboard as DashboardData } from '../api'
import { BillsTable } from '../components/BillsTable'
import { FixedCosts } from '../components/FixedCosts'
import { MonthlyChart } from '../components/MonthlyChart'
import { TopBar } from '../components/TopBar'
import { dayMonth, localDate, money, moneyWhole, monthLong, monthName } from '../format'

export function Dashboard() {
  const [params, setParams] = useSearchParams()
  const month = params.get('month') ?? undefined
  const [data, setData] = useState<DashboardData | null>(null)
  const [loadedFor, setLoadedFor] = useState<string | undefined | null>(null)
  const [error, setError] = useState('')

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

  return (
    <div className="page">
      <TopBar sync={data?.sync} />
      {error && <div className="notice glass error">{error}</div>}
      {data && (
        <main>
          <FixedCosts fixed={data.fixed} />

          <div style={{ opacity: loadedFor === month ? 1 : 0.6, transition: 'opacity 0.15s' }}>
            <section className="grid two">
              <MonthCard data={data} go={go} />
              <div className="card glass">
                <h2>Bills by month</h2>
                <MonthlyChart history={data.history} selected={data.month} onSelect={go} />
              </div>
            </section>

            <section className="card glass" style={{ marginTop: 16 }}>
              <div className="card-head">
                <h2>{monthName(data.month)} bills</h2>
                {data.needs_review > 0 && (
                  <span className="chip warn">
                    ⚠ {data.needs_review} {data.needs_review === 1 ? 'needs' : 'need'} an amount
                  </span>
                )}
              </div>
              <BillsTable bills={data.bills} onChanged={load} />
              {data.statements.count > 0 && (
                <p className="small muted" style={{ margin: '12px 0 0' }}>
                  + {data.statements.count} card statement{data.statements.count > 1 ? 's' : ''} (
                  {money(data.statements.total)}) not counted: that spending comes from the bank.
                </p>
              )}
            </section>
          </div>
        </main>
      )}
    </div>
  )
}

/** The selected month: what's due, what's left, and how it compares with the fixed monthly cost. */
function MonthCard({ data, go }: { data: DashboardData; go: (m: string) => void }) {
  const isCurrent = data.month === data.current_month
  const paid = data.total - data.remaining
  // a month's bills only come from email, so compare with the emailed part of the fixed bills
  const usual = data.fixed?.items.filter((i) => i.source === 'email').reduce((sum, i) => sum + i.monthly, 0)
  const diff = usual ? data.total - usual : null
  const daysUntil = data.next_bill?.due_date
    ? Math.round((localDate(data.next_bill.due_date).getTime() - localDate(data.today).getTime()) / 86_400_000)
    : null

  return (
    <div className="card glass month-card">
      <div className="card-head">
        <h2 className="month-title">{monthLong(data.month)}</h2>
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

      <div className="split">
        <div>
          <div className="label">Bills due</div>
          <div className="tile-value">{money(data.total)}</div>
          <div className="small muted">
            {data.count} {data.count === 1 ? 'bill' : 'bills'}
          </div>
        </div>
        <div>
          <div className="label">Still to pay</div>
          <div className="tile-value">{data.remaining > 0 ? money(data.remaining) : 'Nothing'}</div>
          <div className="small muted">
            {data.next_bill
              ? `Next: ${data.next_bill.biller}, ${dayMonth(data.next_bill.due_date)}${
                  daysUntil === 0 ? ' (today)' : daysUntil === 1 ? ' (tomorrow)' : daysUntil ? ` (in ${daysUntil} days)` : ''
                }`
              : isCurrent
                ? 'No more bills due this month'
                : 'All due dates have passed'}
          </div>
        </div>
      </div>

      {data.total > 0 && (
        <>
          <div className="progress" title="Bills whose due date has passed" aria-hidden>
            <div style={{ width: `${(paid / data.total) * 100}%` }} />
          </div>
          <div className="small muted">
            {money(paid)} of {money(data.total)} already paid
            {diff !== null && usual && (
              <>
                {' · '}
                {diff <= 0 ? '▼' : '▲'} {moneyWhole(Math.abs(diff))} {diff <= 0 ? 'below' : 'above'} your{' '}
                {moneyWhole(usual)} monthly average
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}
