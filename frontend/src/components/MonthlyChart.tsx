import { useState } from 'react'
import { moneyWhole, monthShort, monthShortYear, niceTicks, money } from '../format'

interface Props {
  history: { month: string; total: number }[]
  selected: string
  onSelect: (month: string) => void
}

/** Bills per due month, single series: one hue, no legend, value on the selected cap,
 *  every value on hover/focus (and in the month's table). */
export function MonthlyChart({ history, selected, onSelect }: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const ticks = niceTicks(Math.max(...history.map((h) => h.total), 0))
  const top = ticks[ticks.length - 1]
  const pct = (v: number) => (v / top) * 100

  return (
    <div className="colchart">
      <div className="plot" role="img" aria-label="Bills per month for the last 12 months">
        {ticks.map((t) => (
          <div key={t} className="gridline" style={{ bottom: `${pct(t)}%` }}>
            <span>{moneyWhole(t)}</span>
          </div>
        ))}
        <div className="cols" onPointerLeave={() => setHover(null)}>
          {history.map((h, i) => (
            <button
              key={h.month}
              className="col"
              aria-label={`${monthShortYear(h.month)}: ${money(h.total)}`}
              aria-pressed={h.month === selected}
              onPointerEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              onClick={() => onSelect(h.month)}
            >
              {h.month === selected && h.total > 0 && (
                <span className="cap" style={{ bottom: `calc(${pct(h.total)}% + 4px)` }}>
                  {moneyWhole(h.total)}
                </span>
              )}
              {h.total > 0 && <div className="bar" style={{ height: `${pct(h.total)}%` }} />}
            </button>
          ))}
        </div>
        {hover !== null && (
          <div
            className="tooltip glass"
            style={{
              left: `${((hover + 0.5) / history.length) * 100}%`,
              top: `${100 - pct(history[hover].total)}%`,
            }}
          >
            <strong>{money(history[hover].total)}</strong>
            <span className="small muted">{monthShortYear(history[hover].month)}</span>
          </div>
        )}
      </div>
      <div className="xlabels" aria-hidden>
        {history.map((h) => (
          <span key={h.month} className={h.month === selected ? 'selected' : undefined}>
            {monthShort(h.month)}
          </span>
        ))}
      </div>
    </div>
  )
}
