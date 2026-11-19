import { useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PhoneCall, CalendarX2, CalendarCheck2, BellOff, RefreshCcw, Zap, ClipboardList, Phone, CheckCheck, CalendarClock, CalendarPlus, XCircle } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useFilter, filterParams } from '../lib/store'
import { fmtDateTime, bandColor } from '../lib/format'
import { Card, Empty, Spinner, ErrorBox, BtnSpinner, Modal } from '../components/ui'
import BookingPicker, { type BookingPerson } from '../components/BookingPicker'

interface QueueItem {
  id: string
  kind: 'followup' | 'sla_breach' | 'hot_waiting' | 'ready_to_book' | 'appointment' | 'renewal'
  priority: number
  title: string
  subtitle: string
  next_step?: string
  tier?: 'hot' | 'warm' | 'low' | null
  store_name?: string
  store_id?: string
  service_id?: string
  at: string
  ref_id: string
  person_name?: string
  phone?: string
  score?: number
  stage?: string
  start_hour?: number
  confirmed?: boolean
  plan_end?: string
  days?: number
  waited_min?: number
  sla_min?: number
  hours_overdue?: number
  actions: string[]
}

const KIND_META: Record<string, { label: string; chip: string }> = {
  followup: { label: 'Overdue Follow-Up', chip: 'bg-rose-100 text-rose-700 border-rose-200' },
  sla_breach: { label: 'New Lead - Not Contacted', chip: 'bg-rose-100 text-rose-700 border-rose-200' },
  hot_waiting: { label: 'Hot Lead Waiting', chip: 'bg-amber-100 text-amber-700 border-amber-200' },
  ready_to_book: { label: 'Ready To Book', chip: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  appointment: { label: 'Upcoming Appointment', chip: 'bg-sky-100 text-sky-700 border-sky-200' },
  renewal: { label: 'Renewal', chip: 'bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200' },
}

const TIER_META: Record<string, string> = {
  hot: 'bg-rose-100 text-rose-700 border-rose-200',
  warm: 'bg-amber-100 text-amber-700 border-amber-200',
  low: 'bg-slate-100 text-slate-500 border-slate-200',
}

const ACTION_META: Record<string, { label: string; icon: typeof PhoneCall; kind: 'primary' | 'ghost' | 'danger' }> = {
  log_call: { label: 'Log Call', icon: PhoneCall, kind: 'primary' },
  mark_interested: { label: 'Mark Interested', icon: Zap, kind: 'ghost' },
  complete: { label: 'Mark Done', icon: CheckCheck, kind: 'ghost' },
  reschedule: { label: 'Reschedule', icon: CalendarClock, kind: 'ghost' },
  snooze: { label: 'Snooze 24h', icon: BellOff, kind: 'ghost' },
  book: { label: 'Book', icon: CalendarPlus, kind: 'primary' },
  mark_lost: { label: 'Mark Lost', icon: XCircle, kind: 'danger' },
  confirm: { label: 'Confirm', icon: CalendarCheck2, kind: 'primary' },
  cancel: { label: 'Cancel', icon: CalendarX2, kind: 'danger' },
  offer: { label: 'Send Reminder', icon: RefreshCcw, kind: 'primary' },
}

const LOST_REASONS = ['Price', 'Distance', 'No Response', 'Chose Competitor', 'Timing']

/** Queue kinds map to the API kind used by /queue/action. */
function apiKind(kind: QueueItem['kind']): 'lead' | 'appointment' | 'renewal' {
  return kind === 'appointment' ? 'appointment' : kind === 'renewal' ? 'renewal' : 'lead'
}

export default function ActionQueue() {
  const nav = useNavigate()
  const filter = useFilter()
  const fp = filterParams(filter)
  const qc = useQueryClient()
  const [kindFilter, setKindFilter] = useState('')
  const [booking, setBooking] = useState<(BookingPerson & { renew?: boolean }) | null>(null)
  const [rescheduleFor, setRescheduleFor] = useState<QueueItem | null>(null)
  const [lostFor, setLostFor] = useState<QueueItem | null>(null)
  const [lostReason, setLostReason] = useState('Price')
  const [rescheduleAt, setRescheduleAt] = useState(() => {
    const d = new Date(Date.now() + 24 * 3600 * 1000)
    return d.toISOString().slice(0, 16)
  })

  const { data, isLoading, error } = useQuery({
    queryKey: ['queue', fp.period, fp.store_id],
    queryFn: () => api<{ items: QueueItem[]; counts: Record<string, number>; total: number }>('/queue/today' + qs(fp)),
    refetchInterval: 60_000,
  })

  const act = useMutation({
    mutationFn: (v: { item: QueueItem; action: string; follow_up_at?: string; note?: string; lost_reason?: string }) =>
      api<{ ok: boolean; message: string }>('/queue/action', {
        method: 'POST',
        body: { kind: apiKind(v.item.kind), ref_id: v.item.ref_id, action: v.action,
                follow_up_at: v.follow_up_at, note: v.note, lost_reason: v.lost_reason },
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries()
      setRescheduleFor(null)
      setLostFor(null)
      toast.success(r.message || 'Done')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  function openItem(i: QueueItem) {
    if (i.kind === 'appointment') return nav('/appointments')
    if (i.kind === 'renewal') return nav('/customers')
    nav(`/leads?lead=${i.ref_id}`)
  }

  const items = (data?.items || []).filter((i) => !kindFilter || i.kind === kindFilter)

  return (
    <div className="max-w-4xl">
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <h1 className="text-lg font-extrabold text-slate-800 flex items-center gap-2">
          <ClipboardList size={20} className="text-brand-600" /> Today's Action Queue
        </h1>
        <span className="text-xs text-slate-400">prioritized · recommended next step shown · auto-refreshes every minute</span>
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        <button onClick={() => setKindFilter('')}
          className={`chip border ${!kindFilter ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200 text-slate-600'}`}>
          All ({data?.total ?? 0})
        </button>
        {Object.entries(KIND_META).map(([k, m]) => (
          <button key={k} onClick={() => setKindFilter(kindFilter === k ? '' : k)}
            className={`chip border ${kindFilter === k ? 'bg-brand-600 text-white border-brand-600' : m.chip}`}>
            {m.label} ({data?.counts?.[k] ?? 0})
          </button>
        ))}
      </div>

      {error && <ErrorBox error={error} />}
      {isLoading ? <Spinner /> : !items.length ? (
        <Card className="p-10">
          <Empty icon={<CalendarCheck2 size={30} className="text-emerald-500" />}
            text="All caught up - nothing overdue in this scope" />
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((i, idx) => {
            const meta = KIND_META[i.kind]
            return (
              <Card key={i.id}
                className={`p-3 cursor-pointer hover:border-brand-300 anim-fade-up ${i.priority === 1 ? 'border-l-4 border-l-rose-500' : i.priority === 2 ? 'border-l-4 border-l-amber-400' : 'border-l-4 border-l-slate-300'}`}
                style={{ '--d': `${Math.min(idx, 12) * 30}ms` } as CSSProperties}
                onClick={() => openItem(i)}>
                <div className="flex flex-wrap items-start gap-3">
                  <div className="flex-1 min-w-60">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`chip ${meta.chip}`}>{meta.label}</span>
                      {i.tier && <span className={`chip border ${TIER_META[i.tier]}`}>{i.tier === 'hot' ? 'Hot' : i.tier === 'warm' ? 'Warm' : 'Low'}</span>}
                      {i.score !== undefined && i.score > 0 && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${bandColor(i.score >= 80 ? 'hot' : i.score >= 65 ? 'warm' : 'cold')}`}>{i.score}</span>
                      )}
                      {i.confirmed === false && <span className="chip bg-slate-100 border-slate-200 text-slate-500">unconfirmed</span>}
                    </div>
                    <div className="font-semibold text-slate-800 mt-1">{i.title}</div>
                    <div className="text-xs text-slate-500 mt-0.5">{i.subtitle}</div>
                    {i.next_step && (
                      <div className="text-[11px] text-brand-700 bg-brand-50 border border-brand-100 rounded px-2 py-1 mt-1.5 inline-block">
                        Next: {i.next_step}
                      </div>
                    )}
                    <div className="text-[11px] text-slate-400 mt-1 flex gap-3">
                      <span>due {fmtDateTime(i.at)}</span>
                      {i.phone && (
                        <a href={`tel:${i.phone}`} className="text-brand-600 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <Phone size={11} /> {i.phone}
                        </a>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5" onClick={(e) => e.stopPropagation()}>
                    {i.actions.map((a) => {
                      const am = ACTION_META[a]
                      if (!am) return null
                      const busy = act.isPending && act.variables?.item.id === i.id && act.variables?.action === a
                      const cls = am.kind === 'primary' ? 'btn-primary btn-sm' : am.kind === 'danger' ? 'btn-danger btn-sm' : 'btn-ghost btn-sm'
                      return (
                        <button key={a} className={cls} disabled={act.isPending}
                          title={a === 'book' && i.kind === 'renewal' ? 'Book the renewal' : undefined}
                          onClick={() => {
                            if (a === 'book') {
                              // renewals book the existing customer; leads book via the picker
                              if (i.kind === 'renewal') {
                                setBooking({ id: i.ref_id, name: i.person_name || 'Customer', type: 'customer', renew: true })
                              } else {
                                setBooking({ id: i.ref_id, name: i.person_name || 'Lead', type: 'lead',
                                             store_id: i.store_id, service_id: i.service_id })
                              }
                              return
                            }
                            if (a === 'reschedule') {
                              setRescheduleFor(i)
                              return
                            }
                            if (a === 'mark_lost') {
                              setLostFor(i)
                              return
                            }
                            act.mutate({ item: i, action: a })
                          }}>
                          {busy ? <BtnSpinner /> : <am.icon size={12} />}
                          {a === 'book' && i.kind === 'renewal' ? 'Book Renewal' : am.label}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      {/* Reschedule modal: pick an explicit date and time (logged in the lead history) */}
      <Modal open={!!rescheduleFor} onClose={() => setRescheduleFor(null)}
        title={`Reschedule follow-up - ${rescheduleFor?.person_name || ''}`}>
        <label className="label" htmlFor="fu-at">Follow-up date and time</label>
        <input id="fu-at" type="datetime-local" className="input" value={rescheduleAt}
          onChange={(e) => setRescheduleAt(e.target.value)} />
        <p className="text-xs text-slate-500 mt-2">The lead appears back in this queue at the chosen time. The change is recorded in the lead's activity history.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setRescheduleFor(null)}>Cancel</button>
          <button className="btn-primary" disabled={act.isPending || !rescheduleAt}
            onClick={() => rescheduleFor && act.mutate({ item: rescheduleFor, action: 'reschedule',
              follow_up_at: new Date(rescheduleAt).toISOString() })}>
            {act.isPending ? 'Saving…' : 'Reschedule'}
          </button>
        </div>
      </Modal>

      {/* Mark-lost confirmation: a destructive stage move always asks for a reason */}
      <Modal open={!!lostFor} onClose={() => setLostFor(null)}
        title={`Mark ${lostFor?.person_name || ''} as lost?`}>
        <label className="label" htmlFor="lost-reason">Reason</label>
        <select id="lost-reason" className="select" value={lostReason}
          onChange={(e) => setLostReason(e.target.value)}>
          {LOST_REASONS.map((r) => <option key={r}>{r}</option>)}
        </select>
        <p className="text-xs text-slate-500 mt-2">The lead leaves the pipeline and queue but keeps its full history. It can be reopened later.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setLostFor(null)}>Cancel</button>
          <button className="btn-danger" disabled={act.isPending}
            onClick={() => lostFor && act.mutate({ item: lostFor, action: 'mark_lost', lost_reason: lostReason })}>
            {act.isPending ? 'Saving…' : 'Mark Lost'}
          </button>
        </div>
      </Modal>

      {booking && <BookingPicker open onClose={() => setBooking(null)} person={booking} renew={booking.renew} />}
    </div>
  )
}
