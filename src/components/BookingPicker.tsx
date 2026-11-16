import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, MapPin, IndianRupee, Zap, AlertTriangle } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { fmtHour, fmtMoney, todayISO, addDaysISO } from '../lib/format'
import { Modal, Spinner, Empty } from './ui'

interface Service { _id: string; name: string; duration_hours: number; staff_required: number; base_price: number }
interface StoreOpt {
  store_id: string; store_name: string; distance_km: number | null; far: boolean
  utilization_pct: number; price: { base: number; extra_hours_charge: number; extra_staff_charge: number; subtotal: number; benefit_pct: number; benefit: number; final: number }
  placeable: boolean
}
interface HourCell { hour: number; capacity: number; free: number; employees: { id: string; name: string }[]; status: 'available' | 'full' | 'held' | 'passed' }

export interface BookingPerson {
  id: string
  name: string
  type: 'lead' | 'customer'
  store_id?: string
  service_id?: string
  last_service_id?: string
}

export default function BookingPicker({ open, onClose, person, renew = false, onBooked }: {
  open: boolean
  onClose: () => void
  person: BookingPerson
  renew?: boolean
  onBooked?: (appt: Record<string, unknown>) => void
}) {
  const qc = useQueryClient()
  const [serviceId, setServiceId] = useState(person.service_id || person.last_service_id || '')
  const [storeId, setStoreId] = useState(person.store_id || '')
  const [date, setDate] = useState(todayISO())
  const [hour, setHour] = useState<number | null>(null)
  const [extraHours, setExtraHours] = useState(0)
  const [extraStaff, setExtraStaff] = useState(0)
  const [chosenOpt, setChosenOpt] = useState<StoreOpt | null>(null)
  const [conflict, setConflict] = useState('')

  const { data: services } = useQuery({
    queryKey: ['services'], queryFn: () => api<{ services: Service[] }>('/services'), enabled: open, staleTime: 300_000,
  })
  const service = useMemo(() => services?.services.find((s) => s._id === serviceId), [services, serviceId])

  useEffect(() => {
    if (open) {
      setServiceId(person.service_id || person.last_service_id || services?.services[0]?._id || '')
      setStoreId(person.store_id || '')
      setDate(todayISO())
      setHour(null)
      setExtraHours(0)
      setExtraStaff(0)
      setChosenOpt(null)
      setConflict('')
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // reroute-aware options (BOOK-04): preferred first, alternatives with far-location benefit
  const { data: options, isFetching: optsLoading } = useQuery({
    queryKey: ['appt-options', person.id, serviceId, date, hour],
    queryFn: () => api<{ rerouted: boolean; threshold: number; options: StoreOpt[] }>(
      '/availability/options' + qs({ person_type: person.type, person_id: person.id, service_id: serviceId || undefined, store_id: storeId || undefined, date, start_hour: hour ?? 17 })),
    enabled: open && !!serviceId,
  })

  // hour grid for the chosen store (BOOK-01/02)
  const { data: grid, refetch: refetchGrid, isFetching: gridLoading } = useQuery({
    queryKey: ['availability', storeId, serviceId, date],
    queryFn: () => api<{ offered: boolean; trained: number; hours: HourCell[] }>(
      '/availability' + qs({ store_id: storeId, service_id: serviceId, date })),
    enabled: open && !!storeId && !!serviceId,
  })

  // 5-minute slot hold (BOOK-09)
  useEffect(() => {
    if (hour !== null && storeId && serviceId && open) {
      api('/appointments/hold', { method: 'POST', body: { store_id: storeId, service_id: serviceId, date, start_hour: hour } })
        .catch(() => undefined)
    }
  }, [hour, storeId, serviceId, date, open])

  const book = useMutation({
    mutationFn: () => api('/appointments', {
      method: 'POST',
      body: {
        person_type: person.type, person_id: person.id, store_id: storeId, service_id: serviceId,
        date, start_hour: hour, renew, extra_hours: extraHours, extra_staff: extraStaff,
      },
    }),
    onSuccess: (appt) => {
      void qc.invalidateQueries()
      onBooked?.(appt as Record<string, unknown>)
      onClose()
    },
    onError: async (e) => {
      const err = e as ApiError
      setConflict(err.message)
      if (err.code === 'SLOT_TAKEN') await refetchGrid()
    },
  })

  async function pickEarliest() {
    if (!serviceId) return
    const res = await api<{ earliest: { store_id: string; store_name: string; date: string; start_hour: number } | null }>('/availability/earliest' + qs({ service_id: serviceId }))
    if (res.earliest) {
      setStoreId(res.earliest.store_id)
      setDate(res.earliest.date)
      setHour(res.earliest.start_hour)
      setChosenOpt(null)
      setConflict('')
    }
  }

  const price = useMemo(() => {
    if (!service) return null
    const base = service.base_price
    const extraH = extraHours * base * 0.4
    const extraS = extraStaff * base * 0.3
    const opt = chosenOpt && chosenOpt.store_id === storeId ? chosenOpt : null
    const subtotal = Math.round(base + extraH + extraS)
    const benefit = opt?.price.benefit ? Math.round((opt.price.benefit * (subtotal)) / (subtotal - extraH - extraS || 1)) : 0
    const pct = opt?.price.benefit_pct || 0
    const ben = Math.round(subtotal * pct / 100)
    return { base, extraH, extraS, subtotal, pct, benefit: ben, final: subtotal - ben }
  }, [service, extraHours, extraStaff, chosenOpt, storeId])

  function selectStore(id: string) {
    setStoreId(id)
    setHour(null)
    setConflict('')
    const opt = options?.options.find((o) => o.store_id === id)
    setChosenOpt(opt || null)
  }

  const days = Array.from({ length: 60 }, (_, i) => addDaysISO(todayISO(), i)) // BOOK-08: 60 days ahead

  return (
    <Modal open={open} onClose={onClose} wide title={renew ? `Book Renewal - ${person.name}` : `Book Appointment - ${person.name}`}>
      {/* Step 1: service + earliest */}
      <div className="grid md:grid-cols-3 gap-3 mb-4">
        <div>
          <label className="label">Service</label>
          <select className="select" value={serviceId} onChange={(e) => { setServiceId(e.target.value); setHour(null); setChosenOpt(null) }}>
            {services?.services.map((s) => (
              <option key={s._id} value={s._id}>{s.name} ({s.duration_hours}h · {s.staff_required} staff)</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Day</label>
          <select className="select" value={date} onChange={(e) => { setDate(e.target.value); setHour(null) }}>
            {days.map((d) => <option key={d} value={d}>{d === todayISO() ? `Today (${d})` : d}</option>)}
          </select>
        </div>
        <div className="flex items-end">
          <button className="btn-ghost w-full justify-center" onClick={pickEarliest}>
            <Zap size={14} /> Pick Earliest Available
          </button>
        </div>
      </div>

      {/* Step 2: store options incl. reroute */}
      {options && options.options.length > 0 && (
        <div className="mb-4">
          <div className="label">
            Store {options.rerouted && <span className="text-amber-600 font-semibold">· Reroute active (threshold {options.threshold}%) - the 10% far-location benefit applies over 5 km</span>}
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {options.options.map((o) => (
              <button key={o.store_id} onClick={() => selectStore(o.store_id)}
                className={`text-left border rounded-lg p-2.5 transition
                  ${storeId === o.store_id ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-300' : 'border-slate-200 hover:border-brand-300'}`}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{o.store_name}</span>
                  {o.far && <span className="chip bg-emerald-50 text-emerald-700 border-emerald-200">10% Benefit</span>}
                </div>
                <div className="text-[11px] text-slate-500 flex gap-2 mt-0.5">
                  <span><MapPin size={10} className="inline" /> {o.distance_km !== null ? `${o.distance_km} km` : '-'}</span>
                  <span>Util {o.utilization_pct}%</span>
                  <span className={o.placeable ? 'text-emerald-600' : 'text-amber-600'}>{o.placeable ? 'Free' : 'Busy at hour'}</span>
                </div>
                <div className="text-xs font-semibold text-slate-700 mt-1">{fmtMoney(o.price.final)}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Step 3: hour grid */}
      <div>
        <div className="label">Time Slot - 12 One-Hour Slots, 10 AM To 10 PM</div>
        {!storeId ? <Empty text="Pick a store first" /> : gridLoading ? <Spinner /> : (
          <>
            {!grid?.offered && (
              <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-2 flex gap-2">
                <AlertTriangle size={15} className="shrink-0" />
                This store does not offer the service - not enough trained staff (BR-05). Choose another store.
              </div>
            )}
            <div className="grid grid-cols-6 sm:grid-cols-4 lg:grid-cols-6 gap-1.5">
              {grid?.hours.map((h) => {
                const cls = h.status === 'available' ? 'border-emerald-300 bg-emerald-50 text-emerald-800 hover:border-emerald-500'
                  : h.status === 'passed' ? 'border-slate-100 bg-slate-50 text-slate-300 cursor-not-allowed'
                    : h.status === 'held' ? 'border-amber-300 bg-amber-50 text-amber-700 cursor-not-allowed'
                      : 'border-slate-200 bg-white text-slate-400 cursor-not-allowed'
                return (
                  <button key={h.hour} disabled={h.status !== 'available'} onClick={() => { setHour(h.hour); setConflict('') }}
                    className={`border rounded-lg px-1 py-1.5 text-center transition ${cls} ${hour === h.hour ? 'ring-2 ring-brand-500' : ''}`}>
                    <div className="text-xs font-bold">{fmtHour(h.hour).replace(':00', '')}</div>
                    <div className="text-[10px]">{h.status === 'available' ? `${h.free} free` : h.status === 'full' ? 'Full' : h.status === 'held' ? 'Held' : 'Passed'}</div>
                    {h.status === 'available' && h.employees.length > 0 && (
                      <div className="text-[9px] text-slate-400 truncate">{h.employees.map((e) => e.name.split(' ')[0]).join(', ')}</div>
                    )}
                  </button>
                )
              })}
            </div>
          </>
        )}
      </div>

      {/* Step 4: extras + price breakdown (BR-09) */}
      {hour !== null && service && (
        <div className="mt-4 grid md:grid-cols-2 gap-4 border-t border-slate-100 pt-4">
          <div className="flex gap-4">
            <div>
              <label className="label">Extra Hours ({Math.round(40)}% Of Base Each)</label>
              <div className="flex gap-1">
                {[0, 1, 2].map((n) => (
                  <button key={n} onClick={() => setExtraHours(n)}
                    className={`btn-sm border rounded px-2.5 py-1 ${extraHours === n ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>{n}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="label">Extra Staff ({Math.round(30)}% Of Base Each)</label>
              <div className="flex gap-1">
                {[0, 1, 2].map((n) => (
                  <button key={n} onClick={() => setExtraStaff(Math.min(n, Math.max(0, 3 - service.staff_required)))}
                    className={`btn-sm border rounded px-2.5 py-1 ${extraStaff === n ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>{n}</button>
                ))}
              </div>
            </div>
          </div>
          {price && (
            <div className="bg-slate-50 rounded-lg p-3 text-sm">
              <div className="flex justify-between"><span>Base ({service.name})</span><span className="font-medium">{fmtMoney(price.base)}</span></div>
              {price.extraH > 0 && <div className="flex justify-between text-slate-500"><span>Extra hours × {extraHours}</span><span>+ {fmtMoney(price.extraH)}</span></div>}
              {price.extraS > 0 && <div className="flex justify-between text-slate-500"><span>Extra staff × {extraStaff}</span><span>+ {fmtMoney(price.extraS)}</span></div>}
              <div className="flex justify-between border-t border-slate-200 mt-1 pt-1"><span>Subtotal</span><span className="font-medium">{fmtMoney(price.subtotal)}</span></div>
              {price.benefit > 0 && <div className="flex justify-between text-emerald-600"><span>Far-location benefit ({price.pct}%)</span><span>- {fmtMoney(price.benefit)}</span></div>}
              <div className="flex justify-between font-bold text-base mt-1"><span><IndianRupee size={13} className="inline" /> Final Price</span><span>{fmtMoney(price.final)}</span></div>
            </div>
          )}
        </div>
      )}

      {conflict && (
        <div className="mt-3 text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2 flex gap-2">
          <AlertTriangle size={15} className="shrink-0" /> {conflict} - availability refreshed.
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={hour === null || !storeId || book.isPending}
          onClick={() => book.mutate()}>
          <Clock size={14} /> {book.isPending ? 'Booking…' : renew ? 'Confirm Renewal' : 'Confirm Booking'}
        </button>
      </div>
    </Modal>
  )
}
