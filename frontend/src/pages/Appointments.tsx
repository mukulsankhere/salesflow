import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, CheckCircle2, XCircle, Clock4, UserX, CalendarPlus, ListPlus, Trash2, Send, Search } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useDebouncedValue } from '../lib/hooks'
import { useFilter, filterParams } from '../lib/store'
import { fmtHour, fmtMoney, fmtDate, todayISO, addDaysISO, STATUS_COLORS, STAGE_COLORS, STAGE_LABELS } from '../lib/format'
import { Card, Modal, Tabs, Empty, Spinner, Bar, BtnSpinner } from '../components/ui'
import BookingPicker, { type BookingPerson } from '../components/BookingPicker'

interface Appt {
  _id: string; person: { name: string; phone: string }; person_type: string; lead_id?: string; customer_id?: string
  service_name: string; store_name: string; store_id: string; start_hour: number; duration: number
  employee_names: string[]; status: string; price: { final: number; benefit_pct: number }; renew: boolean
}
interface CalStore {
  store: { _id: string; name: string }
  utilization: { utilization_pct: number }
  rows: { employee_id: string; employee_name: string; shift: string; on_leave: boolean; cells: { hour: number; covered: boolean; appointment: { id: string; person: string; service: string; status: string } | null }[] }[]
}
interface WaitEntry {
  _id: string; person_name: string; service_name: string; store_name: string; status: string
  windows: { date: string; start_hour: number }[]; offer?: { date: string; start_hour: number; expires_at: string } | null; renew: boolean
}

const HOURS = Array.from({ length: 12 }, (_, i) => 10 + i)

// calendar cell tint per appointment status (booked/completed are the normal view;
// no_show/cancelled appear when the status filter asks for them)
const CELL_COLORS: Record<string, string> = {
  booked: 'bg-emerald-100 text-emerald-800 border-emerald-300',
  completed: 'bg-sky-100 text-sky-800 border-sky-300',
  no_show: 'bg-rose-100 text-rose-800 border-rose-300',
  cancelled: 'bg-slate-200 text-slate-600 border-slate-300',
}

export default function Appointments() {
  const qc = useQueryClient()
  const filter = useFilter()
  const fp = filterParams(filter)
  const [date, setDate] = useState(todayISO())
  const [daypart, setDaypart] = useState('all')
  const [status, setStatus] = useState('')
  const [tab, setTab] = useState('calendar')
  const [booking, setBooking] = useState<{ person: BookingPerson; date?: string; hour?: number } | null>(null)
  const [picker, setPicker] = useState<{ storeId: string; date: string; hour: number } | null>(null)
  const [cellDetail, setCellDetail] = useState<{ id: string; person: string; service: string; status: string } | null>(null)
  const [resched, setResched] = useState<Appt | null>(null)
  const [reschedHour, setReschedHour] = useState(10)
  const [reschedDate, setReschedDate] = useState(todayISO())
  const [reschedErr, setReschedErr] = useState('')
  const [reschedBusy, setReschedBusy] = useState(false)

  const { data: list, isLoading } = useQuery({
    queryKey: ['appts', date, daypart, status, fp.store_id],
    queryFn: () => api<{ appointments: Appt[]; today?: string }>('/appointments' + qs({ date, daypart, status: status || undefined, store_id: fp.store_id || undefined })),
  })
  const { data: cal } = useQuery({
    queryKey: ['calendar', date, daypart, status, fp.store_id],
    queryFn: () => api<{ date: string; stores: CalStore[]; hours: { hour: number; label: string }[]; today?: string }>('/appointments/calendar' + qs({ date, store_id: fp.store_id || undefined, daypart, status: status || undefined })),
  })
  const { data: waitlist } = useQuery({
    queryKey: ['waitlist', fp.store_id],
    queryFn: () => api<{ waitlist: WaitEntry[] }>('/waitlist' + qs({ store_id: fp.store_id || undefined })),
    refetchInterval: 60_000,
  })

  const act = useMutation({
    mutationFn: ({ id, act, body }: { id: string; act: string; body?: object }) =>
      api(`/appointments/${id}/${act}`, { method: 'POST', body }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      const msgs: Record<string, string> = {
        complete: 'Visit marked completed - revenue recorded',
        'no-show': 'Marked as no-show',
        cancel: 'Appointment cancelled - slot released, waitlist notified',
      }
      toast.success(msgs[v.act] || 'Updated')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  // waitlist actions with instant feedback
  const waitAct = useMutation({
    mutationFn: ({ id, act }: { id: string; act: 'accept' | 'offer' | 'remove' }) =>
      act === 'remove'
        ? api(`/waitlist/${id}`, { method: 'DELETE' })
        : api(`/waitlist/${id}/${act}`, { method: 'POST' }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      if (v.act === 'accept') toast.success('Offer accepted - appointment booked into the held slot')
      else if (v.act === 'offer') toast.success('Next free slot offered - 5-minute hold started')
      else toast.success('Removed from waitlist')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const scopedStores = cal?.stores || []
  const canBook = ['admin', 'manager', 'sales'].includes(JSON.parse(localStorage.getItem('sf_user') || '{}').roles?.[0])
  // Server-anchored "today": date navigation stays correct even when the
  // visitor's device clock disagrees with the day the demo data was seeded on.
  const [anchor, setAnchor] = useState(todayISO())
  const initialDateRef = useRef(todayISO())
  const serverToday = list?.today || cal?.today
  useEffect(() => {
    if (!serverToday) return
    setAnchor(serverToday)
    // capture BEFORE mutating the ref: the setDate updater runs later, and the
    // ref would already hold the server value by then
    const initial = initialDateRef.current
    setDate(d => (d === initial ? serverToday : d))
    initialDateRef.current = serverToday
  }, [serverToday])

  // quick booking straight from the calendar: pick a lead or customer, slot is prefilled
  function quickBook(storeId: string, date: string, hour: number) {
    setPicker({ storeId, date, hour })
  }

  return (
    <div>
      {/* date navigation */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, -1))}><ChevronLeft size={15} /></button>
        <input type="date" className="input w-40" value={date} min={addDaysISO(anchor, -31)} max={addDaysISO(anchor, 60)} onChange={(e) => setDate(e.target.value)} />
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, 1))}><ChevronRight size={15} /></button>
        <button className={`btn-sm px-3 py-1.5 rounded border ${date === anchor ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`} onClick={() => setDate(anchor)}>Today</button>
        <button className={`btn-sm px-3 py-1.5 rounded border ${date === addDaysISO(anchor, 1) ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`} onClick={() => setDate(addDaysISO(anchor, 1))}>Tomorrow</button>
        <select className="select w-44" value={daypart} onChange={(e) => setDaypart(e.target.value)}>
          <option value="all">All Day</option><option value="morning">Morning (10-2)</option>
          <option value="afternoon">Afternoon (2-6)</option><option value="evening">Evening (6-10)</option>
        </select>
        <select className="select w-36" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All Status</option><option value="booked">Booked</option><option value="completed">Completed</option>
          <option value="no_show">No-Show</option><option value="cancelled">Cancelled</option>
        </select>
        <div className="ml-auto">
          <Tabs active={tab} onChange={setTab} tabs={[{ key: 'calendar', label: 'Store Calendar' }, { key: 'list', label: 'List View', count: list?.appointments.length }, { key: 'waitlist', label: 'Waitlist', count: waitlist?.waitlist.length }]} />
        </div>
      </div>

      {tab === 'calendar' && (
        <div className="space-y-4">
          {!cal ? <Spinner /> : scopedStores.map((s) => (
            <Card key={s.store._id} className="p-4 overflow-x-auto">
              <div className="flex items-center gap-3 mb-2">
                <span className="font-bold text-slate-800">{s.store.name}</span>
                <span className="text-xs text-slate-400">Utilisation vs available employee-hours</span>
                <div className="w-40"><Bar value={s.utilization.utilization_pct} target={85} color={s.utilization.utilization_pct >= 90 ? 'bg-rose-500' : 'bg-brand-500'} /></div>
                <span className="text-xs font-bold">{s.utilization.utilization_pct}%</span>
              </div>
              <table className="w-full min-w-[900px] border-collapse">
                <thead>
                  <tr>
                    <th className="th w-32">Employee</th>
                    {(cal?.hours || []).map((h) => <th key={h.hour} className="th text-center text-[10px]">{h.label.replace(':00', '')}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {s.rows.map((r) => (
                    <tr key={r.employee_id}>
                      <td className="td">
                        <div className="font-medium text-sm">{r.employee_name}</div>
                        <div className="text-[10px] text-slate-400">{r.on_leave ? 'On Leave' : r.shift}</div>
                      </td>
                      {r.cells.map((cell) => (
                        <td key={cell.hour} className="p-0.5">
                          {cell.appointment ? (
                            <button onClick={() => setCellDetail(cell.appointment!)}
                              className={`w-full h-9 rounded text-[9px] px-1 font-medium leading-tight border ${CELL_COLORS[cell.appointment.status] || CELL_COLORS.completed}`}>
                              {cell.appointment.person.split(' ')[0]}<br />{cell.appointment.service.split(' ')[0]}
                            </button>
                          ) : (
                            <button
                              disabled={!canBook || !cell.covered}
                              onClick={() => quickBook(s.store._id, date, cell.hour)}
                              title={!cell.covered ? 'Off shift' : 'Click to book this slot'}
                              className={`w-full h-9 rounded border border-dashed ${cell.covered ? 'border-slate-200 hover:border-brand-400 hover:bg-brand-50' : 'border-slate-100 bg-slate-50'} `} />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {!s.rows.length && <tr><td className="td text-slate-400" colSpan={(cal?.hours?.length || 12) + 1}>No employees at this store</td></tr>}
                </tbody>
              </table>
            </Card>
          ))}
          <div className="text-xs text-slate-400">Empty dashed cells open a quick booking on covered hours; booked cells open details. Bookings run through the server-side capacity engine - double bookings are impossible (unique slot index).</div>
        </div>
      )}

      {tab === 'list' && (isLoading ? <Spinner /> : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead><tr className="bg-slate-50">
              <th className="th">Person</th><th className="th">Service</th><th className="th">Store</th><th className="th">Slot</th>
              <th className="th">Staff</th><th className="th">Price</th><th className="th">Status</th><th className="th text-right">Actions</th>
            </tr></thead>
            <tbody>
              {(list?.appointments || []).map((a) => {
                const busy = act.isPending && act.variables?.id === a._id
                const busyAct = busy ? act.variables?.act : ''
                return (
                <tr key={a._id} className="hover:bg-slate-50">
                  <td className="td"><div className="font-semibold">{a.person.name}</div><div className="text-xs text-slate-400">{a.person_type}{a.renew ? ' · renewal' : ''}</div></td>
                  <td className="td text-sm">{a.service_name}</td>
                  <td className="td text-sm">{a.store_name}</td>
                  <td className="td text-sm">{fmtDate(a._id ? date : date)} {fmtHour(a.start_hour)}<div className="text-xs text-slate-400">{a.duration}h</div></td>
                  <td className="td text-xs">{a.employee_names.join(', ')}</td>
                  <td className="td text-sm font-semibold">{fmtMoney(a.price.final)}{a.price.benefit_pct ? <div className="text-[10px] text-emerald-600">{a.price.benefit_pct}% far benefit</div> : null}</td>
                  <td className="td"><span className={`chip border-transparent ${STATUS_COLORS[a.status]}`}>{a.status.replace('_', '-')}</span></td>
                  <td className="td text-right">
                    {a.status === 'booked' && (
                      <div className="flex justify-end gap-1">
                        <button className="btn-ghost btn-sm text-emerald-700" disabled={busy}
                          onClick={() => act.mutate({ id: a._id, act: 'complete' })}>{busyAct === 'complete' ? <BtnSpinner /> : <CheckCircle2 size={11} />} Complete</button>
                        <button className="btn-ghost btn-sm" disabled={busy} onClick={() => { setResched(a); setReschedHour(a.start_hour); setReschedDate(date); setReschedErr('') }}><Clock4 size={11} /> Reschedule</button>
                        <button className="btn-ghost btn-sm text-rose-600" disabled={busy}
                          onClick={() => act.mutate({ id: a._id, act: 'no-show' })}>{busyAct === 'no-show' ? <BtnSpinner /> : <UserX size={11} />} No-Show</button>
                        <button className="btn-ghost btn-sm text-slate-500" disabled={busy}
                          onClick={() => act.mutate({ id: a._id, act: 'cancel', body: { reason: 'customer request' } })}>{busyAct === 'cancel' ? <BtnSpinner /> : <XCircle size={11} />} Cancel</button>
                      </div>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
          {!list?.appointments.length && <Empty text="No appointments for this day and filter" />}
        </Card>
      ))}

      {tab === 'waitlist' && (
        <div className="space-y-3">
          {(waitlist?.waitlist || []).map((w) => (
            <Card key={w._id} className="p-3 flex flex-wrap items-center gap-3">
              <div className="flex-1">
                <div className="font-semibold text-sm">{w.person_name} {w.renew && <span className="chip bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200">Renewal Priority</span>}</div>
                <div className="text-xs text-slate-400">{w.service_name} @ {w.store_name} · wants {w.windows.map((x) => `${x.date} ${fmtHour(x.start_hour)}`).join(', ')}</div>
              </div>
              {w.status === 'offered' && w.offer && (
                <span className="chip bg-amber-100 text-amber-700 border-amber-200">
                  Offered {w.offer.date} {fmtHour(w.offer.start_hour)} · hold until {new Date(w.offer.expires_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}
                </span>
              )}
              {w.status === 'waiting' && <span className="chip bg-slate-100 border-slate-200 text-slate-500">Waiting</span>}
              <span className="flex gap-1">
                {w.status === 'offered' && (
                  <button className="btn-primary btn-sm"
                    disabled={waitAct.isPending && waitAct.variables?.id === w._id}
                    onClick={() => waitAct.mutate({ id: w._id, act: 'accept' })}>
                    {waitAct.isPending && waitAct.variables?.id === w._id && waitAct.variables?.act === 'accept' ? <BtnSpinner /> : <CheckCircle2 size={11} />} Accept & Book
                  </button>
                )}
                {w.status === 'waiting' && (
                  <button className="btn-ghost btn-sm"
                    disabled={waitAct.isPending && waitAct.variables?.id === w._id}
                    onClick={() => waitAct.mutate({ id: w._id, act: 'offer' })}>
                    {waitAct.isPending && waitAct.variables?.id === w._id && waitAct.variables?.act === 'offer' ? <BtnSpinner /> : <Send size={11} />} Offer Next Slot
                  </button>
                )}
                <button className="btn-ghost btn-sm text-slate-400" title="Remove from waitlist"
                  disabled={waitAct.isPending && waitAct.variables?.id === w._id}
                  onClick={() => waitAct.mutate({ id: w._id, act: 'remove' })}><Trash2 size={11} /></button>
              </span>
            </Card>
          ))}
          {!waitlist?.waitlist.length && <Card className="p-6"><Empty text="Waitlist is empty - it fills up when full stores reroute customers" icon={<ListPlus size={26} />} /></Card>}
        </div>
      )}

      {cellDetail && (
        <Modal open onClose={() => setCellDetail(null)} title={`Appointment - ${cellDetail.person}`}>
          <div className="text-sm space-y-2">
            <div>Service: <b>{cellDetail.service}</b></div>
            <div>Status: <span className={`chip border-transparent ${STATUS_COLORS[cellDetail.status]}`}>{cellDetail.status.replace('_', '-')}</span></div>
            <div className="text-xs text-slate-400">Full details and lifecycle actions are in List View.</div>
            <div className="flex justify-end">
              <button className="btn-primary" onClick={() => { setCellDetail(null); setTab('list') }}>Open List View</button>
            </div>
          </div>
        </Modal>
      )}
      {booking && <BookingPicker open onClose={() => setBooking(null)} person={booking.person}
        initialDate={booking.date} initialHour={booking.hour} />}
      {picker && <PersonPicker storeId={picker.storeId} date={picker.date} hour={picker.hour}
        onClose={() => setPicker(null)}
        onPick={(p) => { setPicker(null); setBooking({ person: p, date: picker.date, hour: picker.hour }) }} />}
      {resched && (
        <Modal open onClose={() => setResched(null)} title={`Reschedule - ${resched.person.name}`}>
          <div className="space-y-3">
            <div><label className="label">New Day</label>
              <input type="date" className="input" value={reschedDate} min={anchor} max={addDaysISO(anchor, 60)} onChange={(e) => setReschedDate(e.target.value)} /></div>
            <div><label className="label">New Hour</label>
              <select className="select" value={reschedHour} onChange={(e) => setReschedHour(Number(e.target.value))}>
                {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
              </select></div>
            {reschedErr && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2">{reschedErr}</div>}
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setResched(null)}>Cancel</button>
              <button className="btn-primary" disabled={reschedBusy} onClick={doReschedule}>
                {reschedBusy ? <><BtnSpinner /> Rescheduling…</> : 'Confirm Reschedule'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )

  // stays open on conflict so the error is visible and retryable
  async function doReschedule() {
    if (!resched || reschedBusy) return
    setReschedBusy(true)
    setReschedErr('')
    try {
      await api(`/appointments/${resched._id}/reschedule`, { method: 'POST', body: { date: reschedDate, start_hour: reschedHour } })
      void qc.invalidateQueries()
      toast.success(`Moved to ${reschedDate} ${fmtHour(reschedHour)}`)
      setResched(null)
    } catch (e) {
      setReschedErr((e as ApiError).message)
    } finally {
      setReschedBusy(false)
    }
  }
}

/* ---------------------------------- quick book: choose the lead or customer for a prefilled slot */
function PersonPicker({ storeId, date, hour, onClose, onPick }: {
  storeId: string; date: string; hour: number
  onClose: () => void
  onPick: (p: BookingPerson) => void
}) {
  const [kind, setKind] = useState<'lead' | 'customer'>('lead')
  const [q, setQ] = useState('')
  const dq = useDebouncedValue(q)
  const leads = useQuery({
    queryKey: ['qp-leads', dq],
    queryFn: () => api<{ leads: { _id: string; name: string; phone_display: string; service_name?: string; store_id?: string; service_id?: string }[] }>(
      '/leads' + qs({ q: dq, limit: 8 })),
  })
  const customers = useQuery({
    queryKey: ['qp-customers', dq],
    queryFn: () => api<{ customers: { _id: string; name: string; phone_display: string; last_service_name?: string; store_id?: string; last_service_id?: string }[] }>(
      '/customers' + qs({ q: dq, limit: 8 })),
  })
  const rows = kind === 'lead' ? (leads.data?.leads || []) : (customers.data?.customers || [])
  const loading = kind === 'lead' ? leads.isLoading : customers.isLoading
  void storeId // store is conveyed through BookingPicker prefill
  return (
    <Modal open onClose={onClose} title={`Quick Book - ${date} ${fmtHour(hour)}`}>
      <p className="text-xs text-slate-500 mb-2">Slot prefilled to {date} at {fmtHour(hour)}. Pick who it is for - search by name or phone.</p>
      <div className="flex flex-wrap gap-2 mb-2">
        <button onClick={() => setKind('lead')}
          className={`btn-sm px-3 py-1.5 rounded border ${kind === 'lead' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>Lead</button>
        <button onClick={() => setKind('customer')}
          className={`btn-sm px-3 py-1.5 rounded border ${kind === 'customer' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>Customer</button>
        <div className="relative flex-1 min-w-40">
          <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input className="input pl-8" placeholder="Search name or phone…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        </div>
      </div>
      <div className="max-h-72 overflow-y-auto space-y-1.5">
        {loading ? <Spinner /> : rows.map((r) => (
          <button key={r._id}
            onClick={() => onPick(kind === 'lead'
              ? { id: r._id, name: r.name, type: 'lead', store_id: (r as { store_id?: string }).store_id, service_id: (r as { service_id?: string }).service_id }
              : { id: r._id, name: r.name, type: 'customer', store_id: (r as { store_id?: string }).store_id, service_id: (r as { last_service_id?: string }).last_service_id })}
            className="w-full text-left border border-slate-200 rounded-lg px-3 py-2 hover:border-brand-400 hover:bg-brand-50/40 flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-slate-800">{r.name}<span className="text-xs text-slate-400 font-normal ml-2">{r.phone_display}</span></span>
            <span className="text-xs text-slate-500">{kind === 'lead' ? (r as { service_name?: string }).service_name : (r as { last_service_name?: string }).last_service_name}</span>
          </button>
        ))}
        {!loading && !rows.length && <Empty text={`No ${kind === 'lead' ? 'leads' : 'customers'} match "${dq}"`} />}
      </div>
    </Modal>
  )
}
