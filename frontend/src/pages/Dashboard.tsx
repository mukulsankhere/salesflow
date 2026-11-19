import { useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { Bot, ArrowUpRight, ArrowDownRight, CalendarPlus, RefreshCcw, ListTodo } from 'lucide-react'
import { api } from '../lib/api'
import { useFilter, filterParams } from '../lib/store'
import { fmtMoney } from '../lib/format'
import { useCountUp } from '../lib/useCountUp'
import { Card, SectionTitle, Spinner, Empty } from '../components/ui'

const DONUT_COLORS = ['#3d6cf2', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#14b8a6', '#64748b']

export default function Dashboard() {
  const nav = useNavigate()
  const filter = useFilter()
  const params = filterParams(filter)
  const [trendDays, setTrendDays] = useState(14)

  const { data: kpis, isLoading } = useQuery({
    queryKey: ['kpis', filter.period, filter.storeId],
    queryFn: () => api<{ kpis: { key: string; label: string; value: number; prev: number; delta: number; delta_pct: number | null; section: string }[] }>(
      '/analytics/kpis' + toQ(params)),
  })
  const { data: trend } = useQuery({
    queryKey: ['trend', trendDays, filter.period, filter.storeId],
    queryFn: () => api<{ trend: { label: string; leads: number; bookings: number }[] }>(
      '/analytics/trend' + toQ({ days: String(trendDays), ...params })),
  })
  const { data: sources } = useQuery({
    queryKey: ['sources', filter.period, filter.storeId],
    queryFn: () => api<{ sources: { source: string; label: string; leads: number; booked: number }[] }>(
      '/analytics/sources' + toQ(params)),
  })
  const { data: queue } = useQuery({
    queryKey: ['queue-strip', filter.period, filter.storeId],
    queryFn: () => api<{ items: { id: string; kind: string; title: string; subtitle: string; next_step?: string; ref_id: string; person_name?: string; phone?: string }[]; counts: Record<string, number>; total: number }>('/queue/today' + toQ(params)),
    refetchInterval: 60_000,
  })
  const { data: outcomes } = useQuery({
    queryKey: ['outcomes-strip', filter.period, filter.storeId],
    queryFn: () => api<{ funnel: { leads: number; contacted: number; booked: number; attended: number; renewed: number; lost: number; booking_rate: number; attendance_rate: number } }>('/leads/outcomes' + toQ(params)),
  })

  const kpiMeta: Record<string, string> = {
    new_leads: '#2563eb', lead_conversion: '#7c3aed', bookings_from_leads: '#059669',
    repeat_customers: '#d97706', revenue: '#0f766e', store_utilization: '#dc2626',
  }

  const qc_ = queue?.counts || {}
  const tiles = [
    { icon: Bot, title: 'Let AI Call Them', desc: 'Aarav dials hot and overdue leads first', to: '/ai-calls', n: (qc_['hot_waiting'] || 0) + (qc_['sla_breach'] || 0) },
    { icon: ListTodo, title: 'AI Follow-Ups', desc: 'Overdue follow-ups the AI is working', to: '/ai-calls', n: qc_['followup'] || 0 },
    { icon: CalendarPlus, title: 'Confirm Today', desc: 'Unconfirmed appointments in the next 4 hours', to: '/appointments', n: qc_['appointment'] || 0 },
    { icon: RefreshCcw, title: 'Close Renewals', desc: 'Plans ending within 5 days', to: '/customers', n: qc_['renewal'] || 0 },
  ]

  return (
    <div className="space-y-5">
      {/* KPI cards - the four lead-centric numbers (repeat customers and utilization
          live on their own pages; this dashboard stays focused on leads) */}
      {isLoading ? <Spinner /> : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {(kpis?.kpis || []).filter((k) => k.key !== 'repeat_customers' && k.key !== 'store_utilization').map((k, ki) => {
            const isMoney = k.key === 'revenue'
            const isPct = k.key === 'lead_conversion' || k.key === 'store_utilization'
            const up = k.delta >= 0
            return (
              <button key={k.key} className="card p-4 text-left hover:border-brand-300 transition anim-fade-up"
                style={{ '--d': `${ki * 60}ms` } as CSSProperties}
                onClick={() => nav(`/${k.section === 'dashboard' ? '' : k.section}`)}>
                <div className="text-xs font-medium text-slate-500">{k.label}</div>
                <KpiNumber k={k} isMoney={isMoney} isPct={isPct} color={kpiMeta[k.key]} />
                <div className={`text-[11px] flex items-center gap-1 mt-0.5 ${up ? 'text-emerald-600' : 'text-rose-500'}`}>
                  {up ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  {k.prev !== 0 ? `${up ? '+' : '-'}${isMoney ? fmtMoney(Math.abs(k.delta)) : `${Math.abs(k.delta)}${isPct ? ' pts' : ''}`} vs previous` : 'first period'}
                </div>
              </button>
            )
          })}
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-5">
        {/* Trend (7/14/30) */}
        <Card className="lg:col-span-2 p-4">
          <SectionTitle title="Leads And Bookings Trend" right={
            <div className="flex gap-1">
              {[7, 14, 30].map((d) => (
                <button key={d} onClick={() => setTrendDays(d)}
                  className={`btn-sm px-2 py-1 rounded border text-xs ${trendDays === d ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200 text-slate-500'}`}>
                  {d}d
                </button>
              ))}
            </div>
          } />
          <div className="h-56">
            <ResponsiveContainer>
              <AreaChart data={trend?.trend || []}>
                <XAxis dataKey="label" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Area type="monotone" dataKey="leads" stroke="#2563eb" fill="#2563eb" fillOpacity={0.15} name="Leads" />
                <Area type="monotone" dataKey="bookings" stroke="#059669" fill="#059669" fillOpacity={0.15} name="Bookings" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Source donut with click-through */}
        <Card className="p-4">
          <SectionTitle title="Lead Sources" subtitle="Click a source to open its leads" />
          <div className="h-56">
            <ResponsiveContainer>
              <PieChart>
                <Pie data={sources?.sources || []} dataKey="leads" nameKey="label"
                  innerRadius={45} outerRadius={70} paddingAngle={2} strokeWidth={0}>
                  {(sources?.sources || []).map((_, i) => <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />)}
                </Pie>
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 10 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(sources?.sources || []).map((s, i) => (
              <button key={s.source} onClick={() => nav(`/leads?source=${s.source}`)}
                className="chip border-slate-200 bg-white text-slate-600 hover:border-brand-400">
                <span className="w-2 h-2 rounded-full" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
                {s.label} · {s.leads}
              </button>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        {/* Needs attention now - live from the action queue */}
        <Card className="p-4">
          <SectionTitle title="Needs Attention Now" subtitle="Top of the AI call queue - Aarav is on it"
            right={<button className="text-xs text-brand-600 font-semibold" onClick={() => nav('/ai-calls')}>Open AI Calls →</button>} />
          <div className="space-y-2">
            {(queue?.items || []).slice(0, 4).map((i) => (
              <button key={i.id} onClick={() => nav(i.kind === 'appointment' ? '/appointments' : i.kind === 'renewal' ? '/customers' : `/leads?lead=${i.ref_id}`)}
                className="w-full text-left border border-slate-200 rounded-lg p-3 hover:border-brand-300">
                <div className="text-sm font-semibold text-slate-800">{i.title}</div>
                <div className="text-xs text-slate-500 mt-0.5 line-clamp-1">{i.next_step || i.subtitle}</div>
              </button>
            ))}
            {!(queue?.items || []).length && <Empty text="Nothing needs attention - every lead is inside its SLA" />}
          </div>
        </Card>

        {/* Leads turning into visits and revenue (LEAD-15 headline) */}
        <Card className="p-4">
          <SectionTitle title="Leads To Visits & Revenue" subtitle="This window's leads and where they ended up"
            right={<button className="text-xs text-brand-600 font-semibold" onClick={() => nav('/leads?tab=analytics')}>Full Report →</button>} />
          {outcomes ? (
            <div>
              <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 mb-3">
                {[['Leads', outcomes.funnel.leads], ['Contacted', outcomes.funnel.contacted],
                  ['Booked', outcomes.funnel.booked], ['Attended', outcomes.funnel.attended],
                  ['Renewed', outcomes.funnel.renewed]].map(([label, n], i) => (
                  <div key={label as string} className={`text-center border rounded-lg py-2 anim-pop ${i <= 3 ? 'border-slate-100' : 'border-fuchsia-100 bg-fuchsia-50/40'}`}
                    style={{ animationDelay: `${i * 50}ms` }}>
                    <div className={`text-lg font-extrabold ${i === 4 ? 'text-fuchsia-700' : 'text-slate-800'}`}>{n}</div>
                    <div className="text-[10px] text-slate-500">{label}</div>
                  </div>
                ))}
              </div>
              <div className="text-xs text-slate-500 flex flex-wrap gap-x-4 gap-y-1">
                <span>Booking rate <b className="text-brand-700">{outcomes.funnel.booking_rate}%</b></span>
                <span>Attendance <b className="text-emerald-700">{outcomes.funnel.attendance_rate}%</b> of bookings</span>
                <span>Lost <b className="text-rose-600">{outcomes.funnel.lost}</b></span>
              </div>
            </div>
          ) : <Spinner />}
          <div className="text-[11px] text-slate-400 mt-3">Full breakdown by store, service, source and staff member in Leads → Analytics.</div>
        </Card>
      </div>

      {/* Today's Action Plan - live counts from the action queue */}
      <div>
        <SectionTitle title="Today's Action Plan" subtitle="The four moves that raise bookings and renewals - live counts" />
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {tiles.map((t) => (
            <button key={t.title} className="card p-4 text-left hover:border-brand-300 transition"
              onClick={() => nav(t.to)}>
              <div className="flex items-start justify-between">
                <t.icon size={18} className="text-brand-600" />
                <span className={`text-lg font-extrabold ${t.n ? 'text-slate-800' : 'text-slate-300'}`}>{t.n}</span>
              </div>
              <div className="font-bold text-sm mt-2">{t.title}</div>
              <div className="text-xs text-slate-500">{t.desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function toQ(params: Record<string, string>): string {
  const u = new URLSearchParams(params)
  const s = u.toString()
  return s ? `?${s}` : ''
}

/** KPI value with an ease-out count-up on load and on filter changes. */
function KpiNumber({ k, isMoney, isPct, color }: { k: { key: string; value: number }; isMoney: boolean; isPct: boolean; color: string }) {
  const v = useCountUp(k.value)
  return (
    <div className="text-xl font-extrabold mt-1 tabular-nums" style={{ color }}>
      {isMoney ? fmtMoney(v) : `${v}${isPct ? '%' : ''}`}
    </div>
  )
}
