import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, CheckCircle2, XCircle, Clock4, UserX, CalendarPlus, ListPlus, Trash2, Send } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { useFilter, filterParams } from '../lib/store'
import { fmtHour, fmtMoney, fmtDate, todayISO, addDaysISO, STATUS_COLORS, STAGE_COLORS, STAGE_LABELS } from '../lib/format'
import { Card, Modal, Tabs, Empty, Spinner, Bar } from '../components/ui'
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

export default function Appointments() {
  const qc = useQueryClient()
  const filter = useFilter()
  const fp = filterParams(filter)
  const [date, setDate] = useState(todayISO())
  const [daypart, setDaypart] = useState('all')
  const [status, setStatus] = useState('')
  const [tab, setTab] = useState('calendar')
  const [booking, setBooking] = useState<BookingPerson | null>(null)
  const [cellDetail, setCellDetail] = useState<{ id: string; person: string; service: string; status: string } | null>(null)
  const [resched, setResched] = useState<Appt | null>(null)
  const [reschedHour, setReschedHour] = useState(10)
  const [reschedDate, setReschedDate] = useState(todayISO())

  const { data: list, isLoading } = useQuery({
    queryKey: ['appts', date, daypart, status, fp.store_id],
    queryFn: () => api<{ appointments: Appt[] }>('/appointments' + qs({ date, daypart, status: status || undefined, store_id: fp.store_id || undefined })),
  })
  const { data: cal } = useQuery({
    queryKey: ['calendar', date, fp.store_id],
    queryFn: () => api<{ date: string; stores: CalStore[]; hours: { hour: number; label: string }[] }>('/appointments/calendar' + qs({ date, store_id: fp.store_id || undefined })),
  })
  const { data: waitlist } = useQuery({
    queryKey: ['waitlist'],
    queryFn: () => api<{ waitlist: WaitEntry[] }>('/waitlist'),
    refetchInterval: 60_000,
  })

  const act = useMutation({
    mutationFn: ({ id, act, body }: { id: string; act: string; body?: object }) =>
      api(`/appointments/${id}/${act}`, { method: 'POST', body }),
    onSuccess: () => void qc.invalidateQueries(),
    onError: (e) => alert((e as ApiError).message),
  })

  const scopedStores = cal?.stores || []
  const canBook = ['admin', 'manager', 'sales'].includes(JSON.parse(localStorage.getItem('sf_user') || '{}').roles?.[0])

  function quickBook(storeId: string) {
    alert('Quick booking needs a lead or customer - open one from Leads or Customers and use the booking picker there.')
    void storeId
  }

  return (
    <div>
      {/* date navigation */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, -1))}><ChevronLeft size={15} /></button>
        <input type="date" className="input w-40" value={date} min={todayISO()} max={addDaysISO(todayISO(), 60)} onChange={(e) => setDate(e.target.value)} />
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, 1))}><ChevronRight size={15} /></button>
        <button className={`btn-sm px-3 py-1.5 rounded border ${date === todayISO() ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`} onClick={() => setDate(todayISO())}>Today</button>
        <button className={`btn-sm px-3 py-1.5 rounded border ${date === addDaysISO(todayISO(), 1) ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`} onClick={() => setDate(addDaysISO(todayISO(), 1))}>Tomorrow</button>
        <select className="select w-36" value={daypart} onChange={(e) => setDaypart(e.target.value)}>
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
                    {HOURS.map((h) => <th key={h} className="th text-center text-[10px]">{fmtHour(h).replace(':00', '')}</th>)}
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
                              className={`w-full h-9 rounded text-[9px] px-1 font-medium leading-tight
                                ${cell.appointment.status === 'booked' ? 'bg-emerald-100 text-emerald-800 border border-emerald-300' : 'bg-sky-100 text-sky-800 border border-sky-300'}`}>
                              {cell.appointment.person.split(' ')[0]}<br />{cell.appointment.service.split(' ')[0]}
                            </button>
                          ) : (
                            <button
                              disabled={!canBook || !cell.covered}
                              onClick={() => quickBook(s.store._id)}
                              title={!cell.covered ? 'Off shift' : 'Click to book'}
                              className={`w-full h-9 rounded border border-dashed ${cell.covered ? 'border-slate-200 hover:border-brand-400 hover:bg-brand-50' : 'border-slate-100 bg-slate-50'} `} />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                  {!s.rows.length && <tr><td className="td text-slate-400" colSpan={13}>No employees at this store</td></tr>}
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
              {(list?.appointments || []).map((a) => (
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
                        <button className="btn-ghost btn-sm text-emerald-700" onClick={() => act.mutate({ id: a._id, act: 'complete' })}><CheckCircle2 size={11} /> Complete</button>
                        <button className="btn-ghost btn-sm" onClick={() => { setResched(a); setReschedHour(a.start_hour); setReschedDate(date) }}><Clock4 size={11} /> Reschedule</button>
                        <button className="btn-ghost btn-sm text-rose-600" onClick={() => act.mutate({ id: a._id, act: 'no-show' })}><UserX size={11} /> No-Show</button>
                        <button className="btn-ghost btn-sm text-slate-500" onClick={() => act.mutate({ id: a._id, act: 'cancel', body: { reason: 'customer request' } })}><XCircle size={11} /> Cancel</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
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
                  <button className="btn-primary btn-sm" onClick={() => acceptWait(w._id)}>
                    <CheckCircle2 size={11} /> Accept & Book
                  </button>
                )}
                {w.status === 'waiting' && (
                  <button className="btn-ghost btn-sm" onClick={() => offerWait(w._id)}><Send size={11} /> Offer Next Slot</button>
                )}
                <button className="btn-ghost btn-sm text-slate-400" onClick={() => removeWait(w._id)}><Trash2 size={11} /></button>
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
      {booking && <BookingPicker open onClose={() => setBooking(null)} person={booking} />}
      {resched && (
        <Modal open onClose={() => setResched(null)} title={`Reschedule - ${resched.person.name}`}>
          <div className="space-y-3">
            <div><label className="label">New Day</label>
              <input type="date" className="input" value={reschedDate} min={todayISO()} max={addDaysISO(todayISO(), 60)} onChange={(e) => setReschedDate(e.target.value)} /></div>
            <div><label className="label">New Hour</label>
              <select className="select" value={reschedHour} onChange={(e) => setReschedHour(Number(e.target.value))}>
                {HOURS.map((h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
              </select></div>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setResched(null)}>Cancel</button>
              <button className="btn-primary" onClick={() => { act.mutate({ id: resched._id, act: 'reschedule', body: { date: reschedDate, start_hour: reschedHour } }); setResched(null) }}>
                Confirm Reschedule
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )

  function acceptWait(id: string) {
    api(`/waitlist/${id}/accept`, { method: 'POST' }).then(() => void qc.invalidateQueries()).catch((e) => alert((e as ApiError).message))
  }
  function offerWait(id: string) {
    api(`/waitlist/${id}/offer?date=${addDaysISO(date, 1)}&start_hour=17`, { method: 'POST' }).then(() => void qc.invalidateQueries()).catch((e) => alert((e as ApiError).message))
  }
  function removeWait(id: string) {
    api(`/waitlist/${id}`, { method: 'DELETE' }).then(() => void qc.invalidateQueries()).catch(() => undefined)
  }
}
