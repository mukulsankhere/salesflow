import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Clock, Users, MapPin, Calculator } from 'lucide-react'
import { api } from '../lib/api'
import { fmtMoney } from '../lib/format'
import { Card, Modal, Row, Empty } from '../components/ui'

interface Service {
  _id: string; name: string; role_required: string; duration_hours: number; staff_required: number
  base_price: number; stores_offering: string[]; trained_count: Record<string, number>
  extra_rules: { extra_hour_pct: number; extra_staff_pct: number }
  stores: { id: string; name: string }[]
}

export default function Products() {
  const [detail, setDetail] = useState<Service | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['services'], queryFn: () => api<{ services: Service[] }>('/services') })
  if (isLoading) return <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">{[...Array(5)].map((_, i) => <div key={i} className="card h-40 animate-pulse" />)}</div>

  return (
    <div>
      <p className="text-sm text-slate-500 mb-4">
        Service catalogue with pricing rules (BR-09): extra hours cost 40% of base each, extra staff 30% each;
        bookings over 5 km from the customer's home store earn a 10% far-location benefit (BR-07).
      </p>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {(data?.services || []).map((s) => (
          <button key={s._id} className="card p-4 text-left hover:border-brand-300" onClick={() => setDetail(s)}>
            <div className="flex items-center justify-between">
              <span className="font-bold text-slate-800">{s.name}</span>
              <span className="text-lg font-extrabold text-brand-700">{fmtMoney(s.base_price)}</span>
            </div>
            <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
              <span><Clock size={12} className="inline" /> {s.duration_hours}h</span>
              <span><Users size={12} className="inline" /> {s.staff_required} staff</span>
              <span>{s.role_required}</span>
            </div>
            <div className="mt-3">
              <div className="text-[11px] text-slate-400 mb-1">Offered At (trained staff ≥ required)</div>
              <div className="flex flex-wrap gap-1">
                {s.stores.map((st) => {
                  const offers = s.stores_offering.includes(st.id)
                  return (
                    <span key={st.id} className={`chip ${offers ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                      {st.name.split(' ')[0]} {offers ? `· ${s.trained_count[st.id]} trained` : '· not offered'}
                    </span>
                  )
                })}
              </div>
            </div>
          </button>
        ))}
        {!data?.services.length && <Empty text="No services configured" />}
      </div>

      {detail && <PriceModal s={detail} onClose={() => setDetail(null)} />}
    </div>
  )
}

function PriceModal({ s, onClose }: { s: Service; onClose: () => void }) {
  const extraH = Math.round(s.base_price * s.extra_rules.extra_hour_pct / 100)
  const extraS = Math.round(s.base_price * s.extra_rules.extra_staff_pct / 100)
  const far = Math.round(s.base_price * 0.9)
  return (
    <Modal open onClose={onClose} title={`${s.name} - Price Breakdown (PROD-02)`}>
      <Row label="Base Price">{fmtMoney(s.base_price)}</Row>
      <Row label={`Extra Hour (+${s.extra_rules.extra_hour_pct}% Of Base)`}>+ {fmtMoney(extraH)}</Row>
      <Row label={`Extra Staff Member (+${s.extra_rules.extra_staff_pct}% Of Base)`}>+ {fmtMoney(extraS)}</Row>
      <Row label="Example: 1 Extra Hour + 1 Extra Staff">= {fmtMoney(s.base_price + extraH + extraS)}</Row>
      <Row label="Far-Location Benefit (>5 km, 10% Off The Total)"><Calculator size={12} className="inline text-emerald-600" /> e.g. {fmtMoney(far)} for base</Row>
      <div className="text-xs text-slate-400 mt-3 flex items-center gap-1"><MapPin size={12} /> The benefit applies automatically when the booking store is more than 5 km from the customer's home store (BR-07).</div>
    </Modal>
  )
}
