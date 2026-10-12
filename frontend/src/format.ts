import type { Payment } from './api'

const currency = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD' })
const wholeCurrency = new Intl.NumberFormat('en-CA', {
  style: 'currency',
  currency: 'CAD',
  maximumFractionDigits: 0,
})

export const money = (value: number | null | undefined) => (value == null ? '—' : currency.format(value))
export const moneyWhole = (value: number) => wholeCurrency.format(value)
const compactCurrency = new Intl.NumberFormat('en-CA', {
  style: 'currency',
  currency: 'CAD',
  notation: 'compact',
  maximumFractionDigits: 1,
})
/** 350000 -> "$350K", for axis labels */
export const moneyCompact = (value: number) => compactCurrency.format(value)

/** "2026-10" -> Date at local midnight on the 1st */
const monthDate = (month: string) => {
  const [y, m] = month.split('-').map(Number)
  return new Date(y, m - 1, 1)
}

/** "2026-10-08" -> local Date (not UTC, so it doesn't shift a day) */
export const localDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d)
}

export const monthLong = (month: string) =>
  monthDate(month).toLocaleDateString('en-CA', { month: 'long', year: 'numeric' })
export const monthName = (month: string) => monthDate(month).toLocaleDateString('en-CA', { month: 'long' })
export const monthShort = (month: string) => monthDate(month).toLocaleDateString('en-CA', { month: 'short' })
export const monthShortYear = (month: string) =>
  monthDate(month).toLocaleDateString('en-CA', { month: 'short', year: 'numeric' })

export const dayMonth = (iso: string | null) =>
  iso ? localDate(iso).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' }) : '—'

export const dateTime = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('en-CA', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : '—'

export function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

/** "Auto-debit from RBC ••321", "Auto-charged to Visa ••4242", "You pay this yourself" */
export function paymentLabel(p: Payment | null): string {
  if (!p) return 'Payment method unknown'
  switch (p.method) {
    case 'bank_auto':
      return `Auto-debit from ${p.detail ?? 'your bank'}`
    case 'card_auto':
      return `Auto-charged to ${p.detail ?? 'a card'}`
    case 'auto':
      return p.detail ? `Automatic payment · ${p.detail}` : 'Automatic payment'
    case 'manual':
      return p.detail ? `You pay this yourself · ${p.detail}` : 'You pay this yourself'
  }
}

/** Short form for table rows */
export function paymentShort(p: Payment | null): string {
  if (!p) return 'Payment method unknown'
  const base = { bank_auto: 'Auto-debit', card_auto: 'Auto-charged', auto: 'Automatic', manual: 'Pay manually' }[p.method]
  return p.detail ? `${base} · ${p.detail}` : base
}

/** Where the payment method came from, for tooltips */
export function paymentSource(p: Payment | null): string {
  if (!p) return 'None of the emails say how this bill is paid. Set it with Edit.'
  if (p.source === 'you') return 'Set by you'
  const when = p.source_date ? `, ${dayMonth(p.source_date)}` : ''
  return `From the email “${(p.source_subject ?? '').trim()}”${when}`
}

export const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/** 1 -> "1st", 22 -> "22nd", 13 -> "13th" */
export function ordinal(n: number): string {
  const suffix = n % 100 >= 11 && n % 100 <= 13 ? 'th' : (['th', 'st', 'nd', 'rd'][n % 10] ?? 'th')
  return `${n}${suffix}`
}

/** Plaid's "FOOD_AND_DRINK" -> "Food & drink" */
export const plaidCategoryLabel = (category: string | null) =>
  category ? capitalize(category.toLowerCase().replace(/_and_/g, ' & ').replace(/_/g, ' ')) : 'Other'

const CATEGORY_LABELS: Record<string, string> = {
  utilities: 'Utilities',
  phone_internet: 'Phone & internet',
  housing: 'Housing & property tax',
  insurance: 'Insurance',
  subscriptions: 'Subscriptions',
  credit_card: 'Credit card',
  investment: 'Investment',
  other: 'Other',
}
export const categoryLabel = (category: string) => CATEGORY_LABELS[category] ?? category

/** Clean axis steps (0 / 1,000 / 2,000 / 3,000) covering `max` in about three steps. */
export function niceTicks(max: number): number[] {
  if (max <= 0) return [0, 100]
  const raw = max / 3
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? 10 * magnitude
  const top = Math.ceil(max / step) * step
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step)
}

/** Clean axis steps covering min..max (either may be negative) in about `steps` steps. */
export function niceRange(min: number, max: number, steps = 3): number[] {
  if (max - min < 1) {
    // a flat line: give it some room above and below
    const pad = Math.max(Math.abs(max) * 0.05, 100)
    min -= pad
    max += pad
  }
  const raw = (max - min) / steps
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? 10 * magnitude
  const lo = Math.floor(min / step) * step
  const hi = Math.ceil(max / step) * step
  return Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, i) => lo + i * step || 0)
}
