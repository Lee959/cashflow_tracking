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

export interface BillPayment {
  date: string
  account: string
  name: string
  pending: boolean
}

export interface Bill {
  id: number
  payment: Payment | null
  paid: BillPayment | null
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

interface FixedCostBase {
  biller: string
  category: string
  monthly: number
  yearly: number
  cadence: string
}

/** One line of the fixed bills: from bill emails, or entered by hand on the Settings page. */
export type FixedCost = FixedCostBase &
  (
    | { source: 'email'; bills: number; last_amount: number; last_due_date: string | null }
    | { source: 'manual'; id: number; amount: number; next_due_date: string | null }
  )

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
  bank: BankSpending | null
  sync: SyncStatus
}

export interface BankTransaction {
  date: string
  name: string
  amount: number
  category: string | null
  account: string
  pending: boolean
}

export interface BankSpending {
  environment: string
  is_test_data: boolean
  total: number
  refunds: number
  income: number
  count: number
  accounts: number
  by_category: { category: string; total: number }[]
  recent: BankTransaction[]
  last_synced_at: string | null
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

export interface ManualCost {
  id: number
  name: string
  category: string
  amount: number
  frequency: string
  due_day: number | null
  next_due_date: string | null
  active: boolean
  monthly: number
}

export interface ManualCostList {
  items: ManualCost[]
  frequencies: string[]
  categories: string[]
}

export interface ManualCostInput {
  name: string
  amount: string
  frequency: string
  category: string
  due_day: number | null
  next_due_date: string | null
  active: boolean
}

export interface BankAccount {
  account_id: string
  name: string
  mask: string | null
  type: 'depository' | 'credit' | 'loan' | 'investment' | string
  subtype: string | null
  currency: string | null
  current_balance: number | null
  available_balance: number | null
  credit_limit: number | null
  statement_balance: number | null
  statement_date: string | null
  minimum_payment: number | null
  payment_due_date: string | null
  is_overdue: boolean | null
}

export interface BankConnection {
  item_id: string
  institution_name: string
  environment: string
  products: string[]
  last_synced_at: string | null
  last_error: string | null
  needs_reconnect: boolean
  transaction_count: number
  accounts: BankAccount[]
}

export interface PlaidStatus {
  environment: 'sandbox' | 'production' | string
  problem: string | null
  items: BankConnection[]
}

export interface PlaidSyncResult {
  item_id: string
  accounts: number
  added: number
  modified: number
  removed: number
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
  if (res.status === 401 && path.startsWith('/api/')) {
    // locked (idle timeout, or locked in another tab): show the lock screen
    window.dispatchEvent(new Event('auth-required'))
  }
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

export type AuthState = 'none' | 'needs_setup' | 'locked' | 'unlocked'

/** Login: launch link -> Touch ID (see app/auth.py). WebAuthn options/responses pass through as JSON. */
export const auth = {
  status: () => request<{ state: AuthState }>('/auth/status'),
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  registerOptions: () => post<any>('/auth/passkey/register/options'),
  registerVerify: (credential: unknown) => post<{ state: AuthState }>('/auth/passkey/register/verify', credential),
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  unlockOptions: () => post<any>('/auth/passkey/unlock/options'),
  unlockVerify: (credential: unknown) => post<{ state: AuthState }>('/auth/passkey/unlock/verify', credential),
  lock: () => post<{ state: AuthState }>('/auth/lock'),
}

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
  plaid: () => request<PlaidStatus>('/api/plaid'),
  plaidLinkToken: () => post<{ link_token: string }>('/api/plaid/link-token'),
  plaidConnect: (body: { public_token: string; institution_name: string | null; institution_id: string | null }) =>
    post<PlaidSyncResult>('/api/plaid/connect', body),
  plaidReconnectToken: (itemId: string) =>
    post<{ link_token: string }>(`/api/plaid/items/${encodeURIComponent(itemId)}/link-token`),
  plaidSync: (itemId: string) => post<PlaidSyncResult>(`/api/plaid/items/${encodeURIComponent(itemId)}/sync`),
  plaidRemove: (itemId: string) =>
    request<{ ok: true }>(`/api/plaid/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' }),
  manualCosts: () => request<ManualCostList>('/api/manual-costs'),
  addManualCost: (body: ManualCostInput) => post<ManualCost>('/api/manual-costs', body),
  updateManualCost: (id: number, body: ManualCostInput) =>
    request<ManualCost>(`/api/manual-costs/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteManualCost: (id: number) => request<{ ok: true }>(`/api/manual-costs/${id}`, { method: 'DELETE' }),
}
