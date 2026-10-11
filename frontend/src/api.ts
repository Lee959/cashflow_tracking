// Typed client for the FastAPI backend (app/api.py).

export interface SyncRun {
  trigger: string
  started_at: string | null
  finished_at: string | null
  ok: boolean
  summary: string
}

export interface SyncStatus {
  gmail_connected: boolean
  running: boolean
  last_run: SyncRun | null
}

export interface Attachment {
  id: number
  filename: string
}

export type PaymentMethod = 'bank_auto' | 'card_auto' | 'auto' | 'manual'

export interface Payment {
  method: PaymentMethod
  detail: string | null
  source: 'email' | 'you'
  source_date: string | null
  source_subject: string | null
}

export interface Bill {
  id: number
  payment: Payment | null
  biller: string
  category: string
  amount: number | null
  due_date: string | null
  upcoming: boolean
  status: 'parsed' | 'statement' | 'needs_review' | 'manual' | 'ignored'
  notes: string
  subject: string
  received_at: string
  attachments: Attachment[]
}

export interface FixedCost {
  biller: string
  category: string
  monthly: number
  yearly: number
  cadence: string
  bills: number
  last_amount: number
  last_due_date: string | null
}

export interface FixedCosts {
  monthly: number
  yearly: number
  from_month: string
  to_month: string
  months: number
  items: FixedCost[]
}

export interface Dashboard {
  month: string
  current_month: string
  prev_month: string
  next_month: string | null
  today: string
  total: number
  count: number
  remaining: number
  next_bill: Bill | null
  fixed: FixedCosts | null
  history: { month: string; total: number }[]
  categories: { category: string; total: number }[]
  bills: Bill[]
  statements: { count: number; total: number }
  needs_review: number
  sync: SyncStatus
}

export interface Biller {
  name: string
  senders: string[]
  category: string
  subject_includes: string[]
}

export interface Settings {
  gmail_account: string | null
  sync: SyncStatus & { interval_hours: number; next_run_at: string | null; runs: SyncRun[] }
  billers: Biller[]
  config_error: string | null
}

export interface Candidate {
  email: string
  domain: string
  name: string
  category: string
  kind: 'bill' | 'card' | 'account' | 'purchase'
  kind_label: string
  cadence: string
  cadence_label: string
  count: number
  content: '' | 'amount' | 'statement_notice' | 'no_amount'
  sample_amount: number | null
  latest_subject: string
  latest_at: string
  duplicate_of: string
  subject_filter: string
  recommended: boolean
  already: boolean
}

export interface ScanResult {
  days: number
  categories: string[]
  groups: Record<'bills' | 'periodic_bills' | 'card_statements' | 'not_bills', Candidate[]>
  skipped: { notifications: number; personal: number }
}

export interface NewSender {
  email: string
  name: string
  category: string
  subject_filter: string
  whole_domain: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  dashboard: (month?: string) => request<Dashboard>(`/api/dashboard${month ? `?month=${month}` : ''}`),
  updateBill: (id: number, body: { amount: string | null; due_date: string | null; ignored: boolean }) =>
    request<{ ok: true }>(`/api/bills/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  setPayment: (biller: string, method: PaymentMethod | null, detail: string | null) =>
    request<{ ok: true; payment: Payment | null }>(`/api/billers/${encodeURIComponent(biller)}/payment`, {
      method: 'PUT',
      body: JSON.stringify({ method, detail }),
    }),
  settings: () => request<Settings>('/api/settings'),
  sync: (body: { months_back?: number; force?: boolean } = {}) => post<SyncRun>('/api/sync', body),
  reparse: () => post<{ summary: string }>('/api/reparse'),
  disconnectGmail: () => post<{ ok: true }>('/api/gmail/disconnect'),
  scanSenders: (days: number, refresh = false) =>
    request<ScanResult>(`/api/senders/scan?days=${days}${refresh ? '&refresh=true' : ''}`),
  addSenders: (senders: NewSender[]) => post<{ added: number }>('/api/senders', { senders }),
}
