import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Clock, Users, MapPin, Calculator, Search } from 'lucide-react'
import { api } from '../lib/api'
import { fmtMoney } from '../lib/format'
import { Card, Modal, Row, Empty } from '../components/ui'

interface Service {
  _id: string; name: string; role_required: string; duration_hours: number; staff_required: number
  base_price: number; stores_offering: string[]; trained_count: Record<string, number>
  extra_rules: { extra_hour_pct: number; extra_staff_pct: number }
  stores: { id: string; name: string }[]
}

const SORTS = [
  { key: 'name', label: 'Name (A-Z)' },
  { key: 'price_up', label: 'Price (Low-High)' },
  { key: 'price_down', label: 'Price (High-Low)' },
  { key: 'duration', label: 'Duration (Longest First)' },
]

export default function Services() {
  const [q, setQ] = useState('')
  const [storeId, setStoreId] = useState('all')
  const [sort, setSort] = useState('name')
  const [detail, setDetail] = useState<Service | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['services'], queryFn: () => api<{ services: Service[] }>('/services') })

  const stores = useMemo(() => data?.services[0]?.stores || [], [data])
  const filtered = useMemo(() => {
    let list = data?.services || []
    if (q.trim()) {
      const t = q.trim().toLowerCase()
      list = list.filter((s) => s.name.toLowerCase().includes(t) || s.role_required.toLowerCase().includes(t))
    }
    if (storeId !== 'all') list = list.filter((s) => s.stores_offering.includes(storeId))
    const sorted = [...list]
    sorted.sort((a, b) => {
      if (sort === 'price_up') return a.base_price - b.base_price
      if (sort === 'price_down') return b.base_price - a.base_price
      if (sort === 'duration') return b.duration_hours - a.duration_hours || b.base_price - a.base_price
      return a.name.localeCompare(b.name)
    })
    return sorted
  }, [data, q, storeId, sort])

  if (isLoading) return <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">{[...Array(5)].map((_, i) => <div key={i} className="card h-40 animate-pulse" />)}</div>

  return (
    <div>
      <p className="text-sm text-slate-500 mb-4">
        Service catalogue with pricing rules (BR-09): extra hours cost 40% of base each, extra staff 30% each;
        bookings over 5 km from the customer's home store earn a 10% far-location benefit (BR-07).
      </p>

      {/* filters - search, offering store, sort (all verified working) */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <div className="relative flex-1 min-w-52 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input className="input pl-9" placeholder="Search services or role…" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search services" />
        </div>
        <select className="select w-48" value={storeId} onChange={(e) => setStoreId(e.target.value)} aria-label="Filter by store">
          <option value="all">All Stores</option>
          {stores.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
        </select>
        <select className="select w-52" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort services">
          {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <span className="chip bg-slate-50 text-slate-500 border-slate-200">{filtered.length} of {data?.services.length || 0}</span>
        {(q || storeId !== 'all' || sort !== 'name') && (
          <button className="btn-ghost btn-sm" onClick={() => { setQ(''); setStoreId('all'); setSort('name') }}>Clear</button>
        )}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.map((s) => (
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
        {!filtered.length && <Empty text={q || storeId !== 'all' ? 'No services match these filters - clear them to see the full catalogue' : 'No services configured'} />}
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
    <Modal open onClose={onClose} title={`${s.name} - Price Breakup (PROD-02)`}>
      <Row label="Base Price">{fmtMoney(s.base_price)}</Row>
      <Row label={`Extra Hour (+${s.extra_rules.extra_hour_pct}% Of Base)`}>+ {fmtMoney(extraH)}</Row>
      <Row label={`Extra Staff Member (+${s.extra_rules.extra_staff_pct}% Of Base)`}>+ {fmtMoney(extraS)}</Row>
      <Row label="Example: 1 Extra Hour + 1 Extra Staff">= {fmtMoney(s.base_price + extraH + extraS)}</Row>
      <Row label="Far-Location Benefit (>5 km, 10% Off The Total)"><Calculator size={12} className="inline text-emerald-600" /> e.g. {fmtMoney(far)} for base</Row>
      <Row label="Cross-Store Overload Offer (AI Calls)"><MapPin size={12} className="inline text-brand-600" /> 10% off nearby, 15% off beyond 5 km</Row>
      <div className="text-xs text-slate-400 mt-3 flex items-center gap-1"><MapPin size={12} /> The benefit applies automatically when the booking store is more than 5 km from the customer's home store (BR-07).</div>
    </Modal>
  )
}
