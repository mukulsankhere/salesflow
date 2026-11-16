export function fmtMoney(paise: number | undefined | null): string {
  if (paise === undefined || paise === null) return '-'
  return `Rs ${Math.round(paise / 100).toLocaleString('en-IN')}`
}

export function fmtHour(h: number): string {
  const hh = h % 12 === 0 ? 12 : h % 12
  return `${hh}:00 ${h < 12 ? 'AM' : 'PM'}`
}

export function fmtDate(d: string | undefined | null): string {
  if (!d) return '-'
  const s = d.slice(0, 10)
  const dt = new Date(s + 'T00:00:00')
  return dt.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtDateTime(d: string | undefined | null): string {
  if (!d) return '-'
  const dt = new Date(d)
  if (isNaN(dt.getTime())) return d
  return dt.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

export function todayISO(): string {
  const d = new Date()
  const off = d.getTimezoneOffset()
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10)
}

export function addDaysISO(iso: string, days: number): string {
  const d = new Date(iso + 'T00:00:00')
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

export function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export const STAGE_LABELS: Record<string, string> = {
  new: 'New Lead', contacted: 'Contacted', interested: 'Interested',
  offered: 'Appt. Offered', booked: 'Booked', lost: 'Lost',
}

export const STAGE_COLORS: Record<string, string> = {
  new: 'bg-sky-100 text-sky-700 border-sky-200',
  contacted: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  interested: 'bg-violet-100 text-violet-700 border-violet-200',
  offered: 'bg-amber-100 text-amber-700 border-amber-200',
  booked: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  lost: 'bg-slate-200 text-slate-600 border-slate-300',
}

export const SEGMENT_COLORS: Record<string, string> = {
  vip: 'bg-amber-100 text-amber-800 border-amber-200',
  loyal: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  new: 'bg-sky-100 text-sky-700 border-sky-200',
  at_risk: 'bg-orange-100 text-orange-700 border-orange-200',
  inactive: 'bg-slate-200 text-slate-600 border-slate-300',
  renewal_due: 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200',
  expired_0_7: 'bg-rose-100 text-rose-700 border-rose-200',
  expired_8_21: 'bg-rose-100 text-rose-700 border-rose-200',
  expired_22p: 'bg-slate-200 text-slate-600 border-slate-300',
}

export const STATUS_COLORS: Record<string, string> = {
  booked: 'bg-emerald-100 text-emerald-700',
  completed: 'bg-sky-100 text-sky-700',
  no_show: 'bg-rose-100 text-rose-700',
  cancelled: 'bg-slate-200 text-slate-600',
}

export function bandColor(band: string): string {
  return band === 'hot' ? 'bg-rose-100 text-rose-700'
    : band === 'warm' ? 'bg-amber-100 text-amber-700'
      : 'bg-slate-100 text-slate-600'
}
