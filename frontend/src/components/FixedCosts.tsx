import { Link } from 'react-router'
import type { FixedCosts as FixedCostsData } from '../api'
import { capitalize, dayMonth, money, moneyWhole, monthShortYear } from '../format'

/** What a usual month's bills cost, and which bills make it up. Secondary to this month's spending. */
export function FixedCosts({ fixed }: { fixed: FixedCostsData | null }) {
  if (!fixed || !fixed.items.length)
    return (
      <section className="card glass">
        <h2>Your usual month</h2>
        <p className="muted small">
          No bills yet. Connect Gmail and pick your bill senders in Settings, or add fixed costs there by hand.
        </p>
      </section>
    )

  const largest = fixed.items[0].monthly
  const byHand = fixed.items.filter((i) => i.source === 'manual').length

  return (
    <section className="card glass" aria-label="Your usual month">
      <div className="card-head">
        <h2>Your usual month</h2>
        <span className="small muted">{moneyWhole(fixed.yearly)} a year</span>
      </div>
      <div className="usual-figure">
        {money(fixed.monthly)}
        <span className="per">/ month on average</span>
      </div>

      <ul className="fc-list">
        {fixed.items.map((item) => (
          <li key={item.source === 'manual' ? `manual-${item.id}` : item.biller} className="fc-row">
            <div className="fc-head">
              <span className="fc-name">{item.biller}</span>
              <span className="fc-value">
                {money(item.monthly)}
                <small>/mo</small>
              </span>
            </div>
            <div className="fc-bar" aria-hidden>
              <div style={{ width: `${(item.monthly / largest) * 100}%` }} />
            </div>
            <div className="fc-meta">
              {capitalize(item.cadence)} ·{' '}
              {item.source === 'manual' ? (
                <>
                  {money(item.amount)}
                  {item.next_due_date && <> due {dayMonth(item.next_due_date)}</>} · entered by hand
                </>
              ) : (
                <>
                  last {money(item.last_amount)}
                  {item.last_due_date && <> due {dayMonth(item.last_due_date)}</>}
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="small muted" style={{ margin: '14px 0 0' }}>
        {byHand < fixed.items.length && (
          <>
            Bills due {monthShortYear(fixed.from_month)} – {monthShortYear(fixed.to_month)}, with bills that come every
            few months spread across the months.{' '}
          </>
        )}
        {byHand ? (
          `Includes ${byHand} ${byHand === 1 ? 'cost' : 'costs'} entered by hand.`
        ) : (
          <>
            Payments that never email a bill can be added in <Link to="/settings">Settings</Link>.
          </>
        )}
      </p>
    </section>
  )
}
