import { useEffect, useState, type CSSProperties } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, CalendarPlus, RefreshCcw, MessageSquarePlus, Megaphone, Pencil, Link2, Phone, MessageCircle, CalendarDays, IndianRupee, ShieldCheck, Circle } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useDebouncedValue } from '../lib/hooks'
import { useFilter, filterParams } from '../lib/store'
import { fmtMoney, fmtDate, fmtDateTime, SEGMENT_COLORS } from '../lib/format'
import { Card, Modal, Empty, Spinner, Tabs, Row, ErrorBox, BtnSpinner } from '../components/ui'
import BookingPicker, { type BookingPerson } from '../components/BookingPicker'

interface Customer {
  _id: string; name: string; phone_display: string; store_name?: string; store_id?: string
  last_service_name?: string; last_service_id?: string; visits: number; spend: number
  spend_display: string; segment: string; segment_label: string; next_best_action: string
  plan?: { start: string; end: string } | null; consent?: Record<string, boolean>
  last_visit_at?: string; no_shows?: number; source?: string
}

interface TimelineEvent {
  at: string; type: string; icon?: string; direction?: string
  title: string; detail?: string | null; by?: string | null
}

function timelineDot(type: string): string {
  if (type.startsWith('appointment_completed')) return 'bg-emerald-100 border-emerald-200 text-emerald-700'
  if (type.startsWith('appointment_no_show') || type.startsWith('appointment_cancelled')) return 'bg-rose-100 border-rose-200 text-rose-600'
  if (type.startsWith('appointment')) return 'bg-sky-100 border-sky-200 text-sky-700'
  if (type.startsWith('message_call')) return 'bg-indigo-100 border-indigo-200 text-indigo-700'
  if (type.startsWith('message')) return 'bg-emerald-100 border-emerald-200 text-emerald-700'
  if (type.startsWith('campaign_replied')) return 'bg-fuchsia-100 border-fuchsia-200 text-fuchsia-700'
  if (type.startsWith('campaign')) return 'bg-violet-100 border-violet-200 text-violet-700'
  if (type.startsWith('payment')) return 'bg-amber-100 border-amber-200 text-amber-700'
  return 'bg-slate-100 border-slate-200 text-slate-500'
}

function timelineIcon(icon?: string) {
  const s = 11
  if (icon === 'phone') return <Phone size={s} />
  if (icon === 'chat') return <MessageCircle size={s} />
  if (icon === 'calendar') return <CalendarDays size={s} />
  if (icon === 'megaphone') return <Megaphone size={s} />
  if (icon === 'wallet') return <IndianRupee size={s} />
  if (icon === 'shield') return <ShieldCheck size={s} />
  return <Circle size={s} />
}

export default function Customers() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const [tab, setTab] = useState('list')
  const [q, setQ] = useState('')
  const dq = useDebouncedValue(q)
  const [segment, setSegment] = useState('')
  const [page, setPage] = useState(1)
  const [params] = useSearchParams()
  const [detailId, setDetailId] = useState<string | null>(params.get('customer'))

  // searching restarts at page 1
  useEffect(() => { setPage(1) }, [dq])

  const { data: segs } = useQuery({
    queryKey: ['cust-segments', fp.period, fp.store_id],
    queryFn: () => api<{ segments: { key: string; label: string; count: number }[] }>('/customers/segments' + qs(fp)),
  })
  const { data: list, isLoading } = useQuery({
    queryKey: ['customers', fp.period, fp.store_id, segment, dq, page],
    queryFn: () => api<{ customers: Customer[]; total: number }>('/customers' + qs({ ...fp, segment: segment || undefined, q: dq || undefined, page, limit: 15 })),
  })

  return (
    <div>
      <Tabs active={tab} onChange={setTab} tabs={[
        { key: 'list', label: 'All Customers', count: list?.total },
        { key: 'segments', label: 'Segments' },
      ]} />
      {tab === 'segments' && (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {(segs?.segments || []).map((s) => (
            <button key={s.key} onClick={() => { setSegment(s.key === segment ? '' : s.key); setTab('list'); setPage(1) }}
              className={`card p-4 text-left hover:border-brand-300 transition ${segment === s.key ? 'ring-2 ring-brand-400' : ''}`}>
              <span className={`chip ${SEGMENT_COLORS[s.key] || 'bg-slate-100 border-slate-200 text-slate-600'}`}>{s.label}</span>
              <div className="text-2xl font-extrabold mt-2">{s.count}</div>
              <div className="text-xs text-slate-400">click to open this segment</div>
            </button>
          ))}
        </div>
      )}
      {tab === 'list' && (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="relative flex-1 min-w-52">
              <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
              <input className="input pl-8" placeholder="Search name or phone…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <select className="select w-full sm:w-52" value={segment} onChange={(e) => { setSegment(e.target.value); setPage(1) }}>
              <option value="">All Segments</option>
              {(segs?.segments || []).map((s) => <option key={s.key} value={s.key}>{s.label} ({s.count})</option>)}
            </select>
          </div>
          {isLoading ? <Spinner /> : !list?.customers.length ? <Card className="p-6"><Empty text="No customers match" /></Card> : (
            <>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {list.customers.map((c, ci) => (
                  <button key={c._id} className="card p-4 text-left hover:border-brand-300 anim-fade-up" style={{ '--d': `${Math.min(ci, 10) * 30}ms` } as CSSProperties} onClick={() => setDetailId(c._id)}>
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-slate-800">{c.name}</span>
                      <span className={`chip ${SEGMENT_COLORS[c.segment] || 'bg-slate-100 border-slate-200 text-slate-600'}`}>{c.segment_label}</span>
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">{c.phone_display} · {c.store_name}</div>
                    <div className="grid grid-cols-3 gap-2 mt-3 text-center">
                      <div><div className="text-sm font-bold">{c.visits}</div><div className="text-[10px] text-slate-400">Visits</div></div>
                      <div><div className="text-sm font-bold">{c.spend_display}</div><div className="text-[10px] text-slate-400">Spend</div></div>
                      <div><div className="text-sm font-bold">{c.plan ? fmtDate(c.plan.end) : '-'}</div><div className="text-[10px] text-slate-400">Plan End</div></div>
                    </div>
                    <div className="text-[11px] text-brand-700 bg-brand-50 rounded px-2 py-1 mt-2">{c.next_best_action}</div>
                  </button>
                ))}
              </div>
              <div className="flex items-center justify-between mt-3">
                <span className="text-xs text-slate-400">{list.total} customers</span>
                <div className="flex gap-1">
                  <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
                  <button className="btn-ghost btn-sm" disabled={page * 15 >= list.total} onClick={() => setPage(page + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
        </>
      )}
      {detailId && <CustomerDetail id={detailId} onClose={() => { setDetailId(null); window.history.replaceState({}, '', '/customers') }} />}
    </div>
  )
}

function CustomerDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [booking, setBooking] = useState<BookingPerson & { renew: boolean } | null>(null)
  const [msgOpen, setMsgOpen] = useState(false)
  const [msgText, setMsgText] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const { data: c, isLoading } = useQuery({
    queryKey: ['customer', id],
    queryFn: () => api<Customer & {
      created_at?: string
      interactions: { _id: string; channel: string; direction: string; content: string; at: string }[]
      appointments: { _id: string; date: string; start_hour: number; service_name: string; status: string; store_name: string; price: { final: number } }[]
      payments: { _id: string; amount: number; method: string; status: string; paid_at?: string }[]
      source_lead?: { _id: string; name: string; stage: string } | null
    }>(`/customers/${id}`),
  })
  const { data: timeline } = useQuery({
    queryKey: ['customer-timeline', id],
    queryFn: () => api<{ timeline: TimelineEvent[] }>(`/customers/${id}/timeline`),
  })
  const consent = useMutation({
    mutationFn: (body: { channel: string; action: string }) => api(`/customers/${id}/consent`, { method: 'POST', body }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      toast.success(`${c?.name || 'Customer'} ${v.action === 'opt-in' ? `opted in to ${v.channel}` : `opted out of ${v.channel}`}`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const sendMessage = useMutation({
    mutationFn: () => api(`/customers/${id}/interactions`, {
      method: 'POST',
      body: { channel: 'whatsapp', direction: 'out', outcome: 'whatsapp_sent', content: msgText },
    }),
    onSuccess: () => {
      void qc.invalidateQueries()
      setMsgOpen(false)
      toast.success(`Message queued to ${c?.name || 'customer'} (consent and quiet hours enforced)`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading || !c) return <Modal open onClose={onClose} title="Customer"><Spinner /></Modal>

  return (
    <Modal open onClose={onClose} wide title={`Customer 360 - ${c.name}`}>
      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${SEGMENT_COLORS[c.segment] || 'bg-slate-100 border-slate-200 text-slate-600'}`}>{c.segment_label}</span>
            <span className="text-xs text-slate-500">Joined {fmtDate(c.created_at)} · Source {c.source?.replace('_', ' ')}</span>
          </div>
          <div className="bg-brand-50 border border-brand-100 rounded-lg px-3 py-2 text-sm text-brand-800">
            Next Best Action: <b>{c.next_best_action}</b>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className="btn-ghost btn-sm" onClick={() => { setMsgText(`Hi ${c.name}, your plan at ${c.store_name} ends on ${c.plan?.end ? fmtDate(c.plan.end) : '-'} . Renew now and keep your preferred slot!`); setMsgOpen(true) }}>
              <MessageSquarePlus size={12} /> Message Preview
            </button>
            <button className="btn-primary btn-sm" onClick={() => setBooking({ id, name: c.name, type: 'customer', store_id: c.store_id, service_id: c.last_service_id, renew: true })}>
              <RefreshCcw size={12} /> Book Renewal
            </button>
            <button className="btn-ghost btn-sm" onClick={() => setBooking({ id, name: c.name, type: 'customer', store_id: c.store_id, service_id: c.last_service_id, renew: false })}>
              <CalendarPlus size={12} /> Book Appointment
            </button>
            <button className="btn-ghost btn-sm" onClick={() => nav('/marketing', { state: { wizard: { kind: 'customers', segment_key: c.segment, label: c.segment_label } } })}><Megaphone size={12} /> Add To Campaign</button>
            {c.source_lead && <button className="btn-ghost btn-sm" onClick={() => nav(`/leads?lead=${c.source_lead!._id}`)}><Link2 size={12} /> Open Source Lead</button>}
            <button className="btn-ghost btn-sm" onClick={() => setEditOpen(true)}><Pencil size={12} /> Edit</button>
          </div>

          <div className="card p-3">
            <div className="text-sm font-bold mb-1">Profile</div>
            <Row label="Phone">{c.phone_display}</Row>
            <Row label="Home Store">{c.store_name}</Row>
            <Row label="Last Service">{c.last_service_name || '-'}</Row>
            <Row label="Last Visit">{c.last_visit_at ? fmtDateTime(c.last_visit_at) : '-'}</Row>
            <Row label="Visits / No-Shows">{c.visits} / {c.no_shows || 0}</Row>
            <Row label="Spend">{c.spend_display}</Row>
            <Row label="Plan">{c.plan ? `${fmtDate(c.plan.start)} → ${fmtDate(c.plan.end)}` : 'No Plan'}</Row>
          </div>

          <div className="card p-3">
            <div className="text-sm font-bold mb-1">Unified Conversation History (CUST-08)</div>
            <div className="text-[11px] text-slate-400 mb-2">Calls, messages, appointment changes, campaign responses, payments and consent - one timeline per customer.</div>
            <div className="max-h-80 overflow-y-auto pr-1">
              {(timeline?.timeline || []).map((e, idx) => (
                <div key={idx} className="flex gap-2.5">
                  <div className="flex flex-col items-center">
                    <span className={`w-6 h-6 rounded-full flex items-center justify-center border shrink-0 ${timelineDot(e.type)}`}>
                      {timelineIcon(e.icon)}
                    </span>
                    {idx < (timeline?.timeline.length || 0) - 1 && <span className="w-px flex-1 bg-slate-100 my-0.5" />}
                  </div>
                  <div className="flex-1 pb-3 min-w-0">
                    <div className="text-sm text-slate-700 font-medium">{e.title}</div>
                    {e.detail && <div className="text-xs text-slate-500 break-words">{e.detail}</div>}
                    <div className="text-[10px] text-slate-400">{fmtDateTime(e.at)}{e.by ? ` · ${e.by}` : ''}</div>
                  </div>
                </div>
              ))}
              {!(timeline?.timeline || []).length && <Empty text="No history yet" />}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card p-3">
            <div className="text-sm font-bold mb-2">Consent (BR-12)</div>
            {['whatsapp', 'sms'].map((ch) => (
              <div key={ch} className="flex items-center justify-between py-1.5 border-b border-slate-50 text-sm">
                <span className="capitalize text-slate-600">{ch}</span>
                <span className="flex gap-1">
                  <button className={`btn-sm px-2 py-0.5 rounded border ${c.consent?.[ch] ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white border-slate-200 text-slate-500'}`}
                    disabled={consent.isPending && consent.variables?.channel === ch && consent.variables?.action === 'opt-in'}
                    onClick={() => consent.mutate({ channel: ch, action: 'opt-in' })}>
                    {consent.isPending && consent.variables?.channel === ch && consent.variables?.action === 'opt-in' ? <BtnSpinner className="w-2.5 h-2.5 border" /> : null} Opt In
                  </button>
                  <button className={`btn-sm px-2 py-0.5 rounded border ${!c.consent?.[ch] ? 'bg-rose-600 text-white border-rose-600' : 'bg-white border-slate-200 text-slate-500'}`}
                    disabled={consent.isPending && consent.variables?.channel === ch && consent.variables?.action === 'opt-out'}
                    onClick={() => consent.mutate({ channel: ch, action: 'opt-out' })}>
                    {consent.isPending && consent.variables?.channel === ch && consent.variables?.action === 'opt-out' ? <BtnSpinner className="w-2.5 h-2.5 border" /> : null} Opt Out
                  </button>
                </span>
              </div>
            ))}
          </div>
          {!!c.payments.length && (
            <div className="card p-3">
              <div className="text-sm font-bold mb-2">Payments (Revenue From Paid - BR-24)</div>
              {c.payments.slice(0, 8).map((p) => (
                <div key={p._id} className="flex justify-between text-xs py-1 border-b border-slate-50">
                  <span className="text-slate-500">{fmtDateTime(p.paid_at)} · {p.method}</span>
                  <span className="font-semibold">{fmtMoney(p.amount)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {booking && <BookingPicker open onClose={() => setBooking(null)} person={booking} renew={booking.renew} />}
      <Modal open={msgOpen} onClose={() => setMsgOpen(false)} title="WhatsApp-Style Message Preview">
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg rounded-tl-none px-4 py-3 text-sm max-w-lg">{msgText}</div>
        <div className="mt-3 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setMsgOpen(false)}>Close</button>
          <button className="btn-primary" disabled={sendMessage.isPending} onClick={() => sendMessage.mutate()}>
            {sendMessage.isPending ? <><BtnSpinner /> Queuing…</> : 'Send'}
          </button>
        </div>
      </Modal>
      <EditCustomerModal id={id} open={editOpen} onClose={() => setEditOpen(false)} c={c} />
    </Modal>
  )
}

function EditCustomerModal({ id, open, onClose, c }: { id: string; open: boolean; onClose: () => void; c: Customer }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: c.name, phone_display: c.phone_display, store_id: c.store_id || '', notes: '' })
  const [error, setError] = useState('')
  const { data: stores } = useQuery({ queryKey: ['stores'], queryFn: () => api<{ stores: { _id: string; name: string }[] }>('/stores'), staleTime: 300_000, enabled: open })
  const save = useMutation({
    mutationFn: () => api(`/customers/${id}`, { method: 'PUT', body: form }),
    onSuccess: () => { void qc.invalidateQueries(); toast.success('Customer updated'); onClose() },
    onError: (e) => setError((e as ApiError).message),
  })
  return (
    <Modal open={open} onClose={onClose} title="Edit Customer">
      <div className="space-y-3">
        <div><label className="label">Name</label><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="label">Phone</label><input className="input" value={form.phone_display} onChange={(e) => setForm({ ...form, phone_display: e.target.value })} /></div>
        <div><label className="label">Home Store</label>
          <select className="select" value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
            {stores?.stores.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
      </div>
      {error && <div className="mt-3"><ErrorBox error={new Error(error)} /></div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
      </div>
    </Modal>
  )
}
