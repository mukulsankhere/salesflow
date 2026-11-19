import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, CheckCircle2, Clock4, UserX } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { fmtHour, fmtMoney, fmtDate, todayISO, addDaysISO, STATUS_COLORS } from '../lib/format'
import { Card, Empty, Spinner, BtnSpinner } from '../components/ui'

interface Appt {
  _id: string; person: { name: string; phone: string }; service_name: string; service_id: string
  store_name: string; start_hour: number; duration: number; status: string; price: { final: number }
  employee_names?: string[]
}

export default function MySchedule() {
  const qc = useQueryClient()
  const [date, setDate] = useState(todayISO())
  const { data, isLoading } = useQuery({
    queryKey: ['my-schedule', date],
    queryFn: () => api<{ appointments: (Appt & { store_name?: string; service_name?: string })[]; employee: { name: string; role: string } | null; shift: string }>(`/me/schedule?date=${date}`),
  })

  const act = useMutation({
    mutationFn: ({ id, act }: { id: string; act: string }) => api(`/appointments/${id}/${act}`, { method: 'POST' }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      toast.success(v.act === 'complete' ? 'Visit marked completed' : 'No-show recorded')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading) return <Spinner />
  const appts = data?.appointments || []

  return (
    <div className="max-w-3xl">
      <div className="flex items-center gap-2 mb-4">
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, -1))}><ChevronLeft size={15} /></button>
        <input type="date" className="input w-40" value={date} onChange={(e) => setDate(e.target.value)} />
        <button className="btn-ghost" onClick={() => setDate(addDaysISO(date, 1))}><ChevronRight size={15} /></button>
        <span className="chip bg-brand-50 text-brand-700 border-brand-200">Shift: {data?.shift || '-'}</span>
        <span className="text-sm text-slate-500 ml-2">{data?.employee?.name} · {data?.employee?.role}</span>
      </div>

      <Card className="divide-y divide-slate-100">
        {appts.map((a) => (
          <div key={a._id} className="flex items-center gap-4 px-4 py-3">
            <div className="w-20 text-center">
              <div className="text-lg font-extrabold text-brand-700">{fmtHour(a.start_hour).replace(':00', '')}</div>
              <div className="text-[10px] text-slate-400">{a.duration}h</div>
            </div>
            <div className="flex-1">
              <div className="font-semibold text-slate-800">{a.person.name}</div>
              <div className="text-xs text-slate-500">{a.service_name} @ {a.store_name} · {fmtMoney(a.price.final)}</div>
            </div>
            <span className={`chip border-transparent ${STATUS_COLORS[a.status]}`}>{a.status.replace('_', '-')}</span>
            {a.status === 'booked' && (
              <span className="flex gap-1">
                <button className="btn-ghost btn-sm text-emerald-700" disabled={act.isPending && act.variables?.id === a._id}
                  onClick={() => act.mutate({ id: a._id, act: 'complete' })}>
                  {act.isPending && act.variables?.id === a._id && act.variables?.act === 'complete' ? <BtnSpinner /> : <CheckCircle2 size={12} />} Done
                </button>
                <button className="btn-ghost btn-sm" title="Reschedule (manager only)"
                  onClick={() => toast.info('Rescheduling is manager-only - ask your manager, or use the Appointments page with a manager login.')}>
                  <Clock4 size={12} />
                </button>
                <button className="btn-ghost btn-sm text-rose-600" disabled={act.isPending && act.variables?.id === a._id}
                  onClick={() => act.mutate({ id: a._id, act: 'no-show' })}>
                  {act.isPending && act.variables?.id === a._id && act.variables?.act === 'no-show' ? <BtnSpinner /> : <UserX size={12} />}
                </button>
              </span>
            )}
          </div>
        ))}
        {!appts.length && <Empty text={`No visits scheduled for ${fmtDate(date)}`} />}
      </Card>
      <div className="text-xs text-slate-400 mt-3">Your schedule updates live - new bookings and cancellations appear without reload (RT-02).</div>
    </div>
  )
}
