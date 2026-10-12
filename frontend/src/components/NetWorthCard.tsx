import { useEffect, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Link } from 'react-router'
import { api, type NetWorth, type NetWorthGroup } from '../api'
import { dayMonth, localDate, money, moneyCompact, moneyWhole, niceRange } from '../format'

/** Dashboard card: what you own minus what you owe, how it moved, and what it's made of. */
export function NetWorthCard() {
  const [data, setData] = useState<NetWorth | null | undefined>(undefined)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = () => api.networth().then(setData, (e: Error) => setError(e.message))
    load()
    // balances change when the background fetch runs
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  if (error) return <div className="notice glass error">{error}</div>
  if (data === undefined) return null
  if (data === null) {
    return (
      <section className="card glass" style={{ marginTop: 16 }}>
        <div className="card-head" style={{ marginBottom: 0 }}>
          <h2>Net worth</h2>
          <Link className="btn small" to="/settings">
            Set up
          </Link>
        </div>
        <p className="small muted" style={{ margin: '8px 0 0' }}>
          Shows up once a bank is connected, or once you add your home, a car or a loan in Settings.
        </p>
      </section>
    )
  }

  const own = data.groups.filter((g) => !g.is_debt)
  const owe = data.groups.filter((g) => g.is_debt)
  const scale = Math.max(data.assets, data.debts, 1)
  const change = data.change_30d

  return (
    <section className="card glass" style={{ marginTop: 16 }} aria-label="Net worth">
      <div className="card-head">
        <h2>Net worth</h2>
        {data.is_test_data ? (
          <span className="chip accent" title="Plaid Sandbox: a test bank with made-up balances">
            Sandbox · test data
          </span>
        ) : (
          <span className="small muted">as of {dayMonth(data.as_of)}</span>
        )}
      </div>

      <div className="nw-top">
        <div>
          <div className="nw-figure">{moneyWhole(data.net)}</div>
          {change !== null && data.since ? (
            <div className={`nw-change ${change >= 0 ? 'up' : 'down'}`}>
              {change >= 0 ? '▲' : '▼'} {moneyWhole(Math.abs(change))}{' '}
              <span className="muted">since {dayMonth(data.since)}</span>
            </div>
          ) : (
            <div className="small muted">First recorded {dayMonth(data.history[0]?.date ?? data.as_of)}</div>
          )}
          <dl className="nw-totals">
            <div>
              <dt>You own</dt>
              <dd>{moneyWhole(data.assets)}</dd>
            </div>
            <div>
              <dt>You owe</dt>
              <dd>{moneyWhole(data.debts)}</dd>
            </div>
          </dl>
        </div>
        <NetWorthChart history={data.history} />
      </div>

      <div className="nw-parts">
        <GroupList title="What you own" groups={own} scale={scale} empty="Nothing yet" />
        <GroupList title="What you owe" groups={owe} scale={scale} empty="No debts recorded" debt />
      </div>

      <p className="small muted" style={{ margin: '14px 0 0' }}>
        Bank balances are recorded on every sync, so the line grows a point a day. Add your home, a car or a loan
        elsewhere under Settings → Other assets &amp; debts.
      </p>
    </section>
  )
}

function GroupList({
  title,
  groups,
  scale,
  empty,
  debt = false,
}: {
  title: string
  groups: NetWorthGroup[]
  scale: number
  empty: string
  debt?: boolean
}) {
  return (
    <div>
      <div className="label">{title}</div>
      {groups.length === 0 && <p className="small muted">{empty}</p>}
      <ul className="fc-list nw-groups">
        {groups.map((g) => (
          <li key={g.group} className="fc-row">
            <div className="fc-head">
              <span className="fc-name">{g.label}</span>
              <span className="fc-value">{moneyWhole(g.total)}</span>
            </div>
            <div className={`fc-bar ${debt ? 'debt' : ''}`} aria-hidden>
              <div style={{ width: `${(Math.max(g.total, 0) / scale) * 100}%` }} />
            </div>
            <ul className="nw-items">
              {g.items.map((item, i) => (
                <li key={`${item.name}-${i}`}>
                  <span>
                    {item.name}
                    {item.manual && <span className="muted"> · entered by you</span>}
                  </span>
                  <span className="num">{money(item.balance)}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Net worth over time, one series: a 2px line with a light wash, a crosshair on hover (or arrow keys). */
function NetWorthChart({ history }: { history: { date: string; net: number }[] }) {
  const [active, setActive] = useState<number | null>(null)
  const times = history.map((p) => localDate(p.date).getTime())
  const first = times[0]
  const span = times[times.length - 1] - first
  const values = history.map((p) => p.net)
  const ticks = niceRange(Math.min(...values), Math.max(...values))
  const lo = ticks[0]
  const hi = ticks[ticks.length - 1]
  // x by date (syncs can skip days), y by value; both in % of the plot
  const x = (i: number) => (span ? ((times[i] - first) / span) * 100 : 50)
  const y = (v: number) => 100 - ((v - lo) / (hi - lo)) * 100
  const line = history.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(p.net).toFixed(2)}`).join(' ')
  const area = `${line} L${x(history.length - 1).toFixed(2)},100 L${x(0).toFixed(2)},100 Z`
  const last = history.length - 1
  const shown = active ?? last

  function nearest(e: PointerEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect()
    const at = first + ((e.clientX - box.left) / box.width) * span
    let best = 0
    times.forEach((t, i) => {
      if (Math.abs(t - at) < Math.abs(times[best] - at)) best = i
    })
    setActive(best)
  }

  function keys(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'ArrowLeft') setActive(Math.max((active ?? last) - 1, 0))
    else if (e.key === 'ArrowRight') setActive(Math.min((active ?? last) + 1, last))
    else return
    e.preventDefault()
  }

  const spansYears = localDate(history[0].date).getFullYear() !== localDate(history[last].date).getFullYear()
  const axisDate = (iso: string) =>
    spansYears
      ? localDate(iso).toLocaleDateString('en-CA', { month: 'short', year: 'numeric' })
      : dayMonth(iso)

  return (
    <div className="linechart">
      <div
        className="plot"
        role="img"
        tabIndex={0}
        aria-label={`Net worth from ${dayMonth(history[0].date)} to ${dayMonth(history[last].date)}: ${moneyWhole(
          history[0].net,
        )} to ${moneyWhole(history[last].net)}. Use the arrow keys to read each day.`}
        onPointerMove={span ? nearest : undefined}
        onPointerLeave={() => setActive(null)}
        onKeyDown={keys}
        onBlur={() => setActive(null)}
      >
        {ticks.map((t) => (
          <div key={t} className={`gridline ${t === 0 && lo < 0 ? 'zero' : ''}`} style={{ top: `${y(t)}%` }}>
            <span>{moneyCompact(t)}</span>
          </div>
        ))}
        {history.length > 1 && (
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
            <path d={area} className="wash" />
            <path d={line} className="line" vectorEffect="non-scaling-stroke" />
          </svg>
        )}
        {active !== null && <div className="crosshair" style={{ left: `${x(active)}%` }} />}
        <div className="dot" style={{ left: `${x(shown)}%`, top: `${y(history[shown].net)}%` }} />
        {active !== null && (
          <div
            className="tooltip glass"
            style={{
              left: `${x(active)}%`,
              top: `${y(history[active].net)}%`,
              // keep it inside the card: hang it off the side near the edges, below the point near the top
              transform: `translate(${x(active) < 18 ? '-8px' : x(active) > 82 ? 'calc(-100% + 8px)' : '-50%'}, ${
                y(history[active].net) < 40 ? '14px' : 'calc(-100% - 12px)'
              })`,
            }}
          >
            <strong>{moneyWhole(history[active].net)}</strong>
            <span className="small muted">{dayMonth(history[active].date)}</span>
          </div>
        )}
      </div>
      <div className="xlabels" aria-hidden>
        <span>{axisDate(history[0].date)}</span>
        {history.length > 1 && <span>{axisDate(history[last].date)}</span>}
      </div>
      {history.length === 1 && (
        <p className="small muted" style={{ margin: '6px 0 0' }}>
          One day recorded so far. A new point is added each day the app syncs.
        </p>
      )}
    </div>
  )
}
