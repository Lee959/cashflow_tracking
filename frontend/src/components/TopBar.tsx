import { Link, NavLink } from 'react-router'
import type { SyncStatus } from '../api'
import { timeAgo } from '../format'

/** Floating glass nav. Shows fetch health quietly; only problems get a coloured chip. */
export function TopBar({ sync }: { sync?: SyncStatus }) {
  return (
    <header className="topbar glass">
      <Link to="/" className="brand">
        <span className="brand-dot" aria-hidden />
        Household Bills
      </Link>
      <div className="row" style={{ gap: 12 }}>
        {sync && <SyncBadge sync={sync} />}
        <nav className="nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </div>
    </header>
  )
}

function SyncBadge({ sync }: { sync: SyncStatus }) {
  if (sync.running)
    return (
      <span className="chip accent">
        <span className="spinner" aria-hidden /> Fetching bills…
      </span>
    )
  if (!sync.gmail_connected)
    return (
      <Link to="/settings" className="chip warn">
        ⚠ Gmail not connected
      </Link>
    )
  const last = sync.last_run
  if (last && !last.ok)
    return (
      <Link to="/settings" className="chip warn" title={last.summary}>
        ⚠ Auto-fetch failed
      </Link>
    )
  return <span className="small muted">Updated {timeAgo(last?.finished_at ?? null)}</span>
}
