import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { ArrowUpRight, ArrowDownRight, Activity, PhoneCall, CalendarPlus, RefreshCcw, ListTodo } from 'lucide-react'
import { api } from '../lib/api'
import { useFilter, filterParams, useRt, feedText } from '../lib/store'
import { fmtMoney, timeAgo } from '../lib/format'
import { Card, SectionTitle, Spinner, Empty } from '../components/ui'

const DONUT_COLORS = ['#3d6cf2', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#14b8a6', '#64748b']

export default function Dashboard() {
  const nav = useNavigate()
  const filter = useFilter()
  const params = filterParams(filter)
  const { events } = useRt()
  const [trendDays, setTrendDays] = useState(14)

  const { data: kpis, isLoading } = useQuery({
    queryKey: ['kpis', filter.period, filter.storeId],
    queryFn: () => api<{ kpis: { key: string; label: string; value: number; prev: number; delta: number; delta_pct: number | null; section: string }[] }>(
      '/analytics/kpis' + toQ(params)),
  })
  const { data: trend } = useQuery({
    queryKey: ['trend', trendDays, filter.period, filter.storeId],
    queryFn: () => api<{ trend: { label: string; leads: number; bookings: number }[] }>(
      `/analytics/trend?days=${trendDays}`),
  })
  const { data: sources } = useQuery({
    queryKey: ['sources', filter.period, filter.storeId],
    queryFn: () => api<{ sources: { source: string; label: string; leads: number; booked: number }[] }>(
      '/analytics/sources' + toQ(params)),
  })
  const { data: insights } = useQuery({
    queryKey: ['insights-strip', filter.period, filter.storeId],
    queryFn: () => api<{ tabs: Record<string, { key: string; title: string; text: string; expected_bookings: number; expected_revenue: number }[]> }>('/insights' + toQ(params)),
    refetchInterval: 120_000,
  })
  const { data: feed } = useQuery({
    queryKey: ['events'],
    queryFn: () => api<{ events: { type: string; payload: Record<string, unknown>; at: string }[] }>('/events?limit=12'),
    refetchInterval: 30_000,
  })

  const kpiMeta: Record<string, string> = {
    new_leads: '#2563eb', lead_conversion: '#7c3aed', bookings_from_leads: '#059669',
    repeat_customers: '#d97706', revenue: '#0f766e', store_utilization: '#dc2626',
  }

  const insightList = insights
    ? Object.entries(insights.tabs).flatMap(([tab, list]) => list.map((i) => ({ ...i, tab }))).slice(0, 4)
    : []

  return (
    <div className="space-y-5">
      {/* KPI cards (DASH-01) */}
      {isLoading ? <Spinner /> : (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          {(kpis?.kpis || []).map((k) => {
            const isMoney = k.key === 'revenue'
            const isPct = k.key === 'lead_conversion' || k.key === 'store_utilization'
            const up = k.delta >= 0
            return (
              <button key={k.key} className="card p-4 text-left hover:border-brand-300 transition"
                onClick={() => nav(`/${k.section === 'dashboard' ? '' : k.section}`)}>
                <div className="text-xs font-medium text-slate-500">{k.label}</div>
                <div className="text-xl font-extrabold mt-1" style={{ color: kpiMeta[k.key] }}>
                  {isMoney ? fmtMoney(k.value) : `${k.value}${isPct ? '%' : ''}`}
                </div>
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
        {/* AI insights strip */}
        <Card className="p-4">
          <SectionTitle title="AI Business Insights" subtitle="Expected bookings and revenue across open recommendations"
            right={<button className="text-xs text-brand-600 font-semibold" onClick={() => nav('/insights')}>Open All →</button>} />
          <div className="space-y-2">
            {insightList.map((i) => (
              <button key={i.key} onClick={() => nav('/insights')}
                className="w-full text-left border border-slate-200 rounded-lg p-3 hover:border-brand-300">
                <div className="text-sm font-semibold text-slate-800">{i.title}</div>
                <div className="text-xs text-slate-500 mt-0.5 line-clamp-2">{i.text}</div>
                <div className="text-[11px] mt-1.5 flex gap-3 text-emerald-700 font-medium">
                  <span>+{i.expected_bookings} bookings</span>
                  <span>{fmtMoney(i.expected_revenue)}</span>
                </div>
              </button>
            ))}
            {!insightList.length && <Empty text="No open insights for this filter" />}
          </div>
        </Card>

        {/* Live activity feed (RT-02) */}
        <Card className="p-4">
          <SectionTitle title="Live Activity" subtitle="Server-pushed events - no reload needed" right={
            <span className={`chip ${useRt.getState().connected ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-slate-100 border-slate-200 text-slate-400'}`}>
              <Activity size={11} /> {useRt.getState().connected ? 'Streaming' : 'Polling'}
            </span>
          } />
          <div className="space-y-1.5 max-h-64 overflow-y-auto">
            {events.map((e) => (
              <div key={e.id} className="flex items-start gap-2 text-sm px-2 py-1.5 rounded-lg bg-slate-50">
                <span className="w-1.5 h-1.5 rounded-full bg-brand-500 mt-1.5 shrink-0" />
                <span className="flex-1 text-slate-700">{feedText(e)}</span>
                <span className="text-[10px] text-slate-400 shrink-0">{timeAgo(e.at)}</span>
              </div>
            ))}
            {!events.length && (feed?.events || []).map((e, i) => (
              <div key={i} className="flex items-start gap-2 text-sm px-2 py-1.5 rounded-lg bg-slate-50">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-300 mt-1.5 shrink-0" />
                <span className="flex-1 text-slate-600">{feedText({ id: i, type: e.type, payload: e.payload, at: e.at })}</span>
                <span className="text-[10px] text-slate-400 shrink-0">{timeAgo(e.at)}</span>
              </div>
            ))}
            {!events.length && !feed?.events.length && <Empty text="Waiting for events…" />}
          </div>
        </Card>
      </div>

      {/* Today's Action Plan - four tiles */}
      <div>
        <SectionTitle title="Today's Action Plan" subtitle="The four moves that raise bookings and renewals" />
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { icon: PhoneCall, title: 'Call Hot Leads', desc: 'Score 80+ leads waiting for the first call', to: '/leads', state: { tab: 'hot' } },
            { icon: CalendarPlus, title: 'Offer Appointments', desc: 'Interested leads ready for a slot offer', to: '/leads', state: { tab: 'pipeline' } },
            { icon: RefreshCcw, title: 'Close Renewals', desc: 'Plans ending within 5 days', to: '/customers', state: { tab: 'segments' } },
            { icon: ListTodo, title: 'Clear Follow-Ups', desc: 'Overdue follow-ups leaking bookings', to: '/leads', state: { tab: 'followups' } },
          ].map((t) => (
            <button key={t.title} className="card p-4 text-left hover:border-brand-300 transition"
              onClick={() => nav(t.to, { state: t.state })}>
              <t.icon size={18} className="text-brand-600" />
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
