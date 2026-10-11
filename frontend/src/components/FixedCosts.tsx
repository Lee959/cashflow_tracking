import type { FixedCosts as FixedCostsData } from '../api'
import { dayMonth, money, moneyWhole, monthShortYear } from '../format'

/** The headline: what the household's regular bills cost per month, and which bills make it up. */
export function FixedCosts({ fixed }: { fixed: FixedCostsData | null }) {
  if (!fixed || !fixed.items.length)
    return (
      <section className="hero-panel glass">
        <div>
          <div className="eyebrow">Your fixed bills</div>
          <p className="muted">No bills yet. Connect Gmail and pick your bill senders in Settings.</p>
        </div>
      </section>
    )

  const [dollars, cents] = money(fixed.monthly).split('.')
  const largest = fixed.items[0].monthly

  return (
    <section className="hero-panel glass" aria-label="Your fixed bills">
      <div className="hero-main">
        <div className="eyebrow">Your fixed bills</div>
        <div className="hero-figure">
          {dollars}
          <span className="cents">.{cents}</span>
          <span className="per">/ month</span>
        </div>
        <div className="hero-sub">{moneyWhole(fixed.yearly)} a year</div>
        <p className="small muted hero-note">
          Average of bills due {monthShortYear(fixed.from_month)} – {monthShortYear(fixed.to_month)}. Bills that come
          every few months are spread across the months. Only bills found in Gmail are included.
        </p>
      </div>

      <ul className="fc-list">
        {fixed.items.map((item) => (
          <li key={item.biller} className="fc-row">
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
              {item.cadence[0].toUpperCase() + item.cadence.slice(1)} · last {money(item.last_amount)}
              {item.last_due_date && <> due {dayMonth(item.last_due_date)}</>} ·{' '}
              {Math.round((item.monthly / fixed.monthly) * 100)}% of the total
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
