import type { BankSpending as BankSpendingData } from '../api'
import { dayMonth, money, monthName, plaidCategoryLabel, timeAgo } from '../format'

const TOP_CATEGORIES = 6

/** Everything that left the connected bank accounts and cards this month, by category. */
export function BankSpending({ bank, month }: { bank: BankSpendingData; month: string }) {
  const largest = bank.by_category[0]?.total ?? 1
  return (
    <section className="card glass" style={{ marginTop: 16 }} aria-label="Bank and card spending">
      <div className="card-head">
        <h2>Bank & card spending · {monthName(month)}</h2>
        {bank.is_test_data ? (
          <span className="chip accent" title="Plaid Sandbox: a test bank with made-up transactions">
            Sandbox · test data
          </span>
        ) : (
          <span className="small muted">synced {timeAgo(bank.last_synced_at)}</span>
        )}
      </div>

      <div className="pair">
        <div>
          <div className="label">Spent</div>
          <div className="tile-value">{money(bank.total)}</div>
          <div className="small muted">
            {bank.count} {bank.count === 1 ? 'purchase' : 'purchases'} across {bank.accounts} accounts and cards
            {bank.refunds > 0 && <> · {money(bank.refunds)} refunded</>}
          </div>
        </div>
        <div>
          <div className="label">Money in</div>
          <div className="tile-value">{money(bank.income)}</div>
          <div className="small muted">pay and other income</div>
        </div>
      </div>

      {bank.by_category.length > 0 && (
        <ul className="fc-list">
          {bank.by_category.slice(0, TOP_CATEGORIES).map((c) => (
            <li key={c.category} className="fc-row">
              <div className="fc-head">
                <span className="fc-name">{plaidCategoryLabel(c.category)}</span>
                <span className="fc-value">{money(c.total)}</span>
              </div>
              <div className="fc-bar" aria-hidden>
                <div style={{ width: `${(c.total / largest) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {bank.recent.length > 0 && (
        <div className="txns">
          <div className="label">Latest</div>
          {bank.recent.map((t, i) => (
            <div key={`${t.date}-${t.name}-${i}`} className="txn-row">
              <span className="txn-date">{dayMonth(t.date)}</span>
              <span className="txn-name">
                {t.name}
                <span className="sub">
                  {t.amount < 0 ? 'Refund' : plaidCategoryLabel(t.category)} · {t.account}
                  {t.pending && ' · pending'}
                </span>
              </span>
              <span className="num">{money(t.amount)}</span>
            </div>
          ))}
        </div>
      )}

      <p className="small muted" style={{ margin: '14px 0 0' }}>
        Transfers between your own accounts and credit card payments aren't counted, so nothing is counted twice.
      </p>
    </section>
  )
}
