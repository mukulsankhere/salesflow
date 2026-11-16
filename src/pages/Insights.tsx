import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useQuery } from '@tanstack/react-query'
import { Sparkles, ArrowUpRight, X, Check, TrendingUp, IndianRupee } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { useFilter, filterParams } from '../lib/store'
import { fmtMoney } from '../lib/format'
import { Card, Tabs, Empty, Spinner, SectionTitle } from '../components/ui'

interface Rec {
  key: string; tab: string; title: string; text: string; evidence: Record<string, unknown>
  expected_bookings: number; expected_revenue: number
  action: { type: string; label: string; payload: Record<string, unknown> }
}

const TABS = [
  { key: 'lead', label: 'Lead & Conversion' },
  { key: 'marketing', label: 'Marketing' },
  { key: 'retention', label: 'Customer Retention' },
  { key: 'store', label: 'Store & Team' },
]

export default function Insights() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const qc = useQueryClient()
  const [tab, setTab] = useState('lead')
  const { data, isLoading } = useQuery({
    queryKey: ['insights', fp.period, fp.store_id],
    queryFn: () => api<{ tabs: Record<string, Rec[]>; impact: { expected_bookings: number; expected_revenue: number; open: number }; scope: string }>(
      `/insights?period=${fp.period}${fp.store_id ? `&store_id=${fp.store_id}` : ''}`),
    refetchInterval: 60_000,
  })
  const apply = useMutation({
    mutationFn: ({ scope, key }: { scope: string; key: string }) => api(`/insights/${scope}/${key}/apply`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries(),
  })
  const dismiss = useMutation({
    mutationFn: ({ scope, key }: { scope: string; key: string }) => api(`/insights/${scope}/${key}/dismiss`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries(),
  })

  if (isLoading) return <Spinner />
  const recs = data?.tabs[tab] || []
  const impact = data?.impact

  return (
    <div className="space-y-4">
      {/* impact summary (AI-04) */}
      <div className="grid grid-cols-3 gap-3">
        <Card className="p-4 flex items-center gap-3">
          <TrendingUp className="text-emerald-600" />
          <div><div className="text-xl font-extrabold text-emerald-600">+{impact?.expected_bookings}</div><div className="text-[11px] text-slate-400">Expected Bookings</div></div>
        </Card>
        <Card className="p-4 flex items-center gap-3">
          <IndianRupee className="text-teal-600" size={20} />
          <div><div className="text-xl font-extrabold text-teal-700">{fmtMoney(impact?.expected_revenue || 0)}</div><div className="text-[11px] text-slate-400">Expected Revenue</div></div>
        </Card>
        <Card className="p-4 flex items-center gap-3">
          <Sparkles className="text-brand-600" />
          <div><div className="text-xl font-extrabold">{impact?.open}</div><div className="text-[11px] text-slate-400">Open Recommendations</div></div>
        </Card>
      </div>

      <Tabs active={tab} onChange={setTab} tabs={TABS.map((t) => ({ key: t.key, label: t.label, count: data?.tabs[t.key]?.length }))} />

      <div className="space-y-3">
        {recs.map((r) => (
          <Card key={r.key} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1">
                <div className="font-bold text-slate-800">{r.title}</div>
                <div className="text-sm text-slate-600 mt-1">{r.text}</div>
                <div className="flex flex-wrap gap-2 mt-2 text-xs">
                  {Object.entries(r.evidence).map(([k, v]) => (
                    <span key={k} className="chip bg-slate-50 border-slate-200 text-slate-500">{k.replace(/_/g, ' ')}: {String(v)}</span>
                  ))}
                </div>
                <div className="flex gap-4 mt-2.5 text-sm font-semibold">
                  <span className="text-emerald-600">+{r.expected_bookings} Expected Bookings</span>
                  <span className="text-teal-700">{fmtMoney(r.expected_revenue)} Expected Revenue</span>
                </div>
              </div>
              <div className="flex flex-col items-end gap-2 shrink-0">
                <button className="btn-primary btn-sm" onClick={() => apply.mutate({ scope: data!.scope, key: r.key })}>
                  <Check size={12} /> {r.action.label}
                </button>
                <button className="btn-ghost btn-sm text-slate-400" onClick={() => dismiss.mutate({ scope: data!.scope, key: r.key })}>
                  <X size={12} /> Dismiss
                </button>
              </div>
            </div>
          </Card>
        ))}
        {!recs.length && <Card className="p-6"><Empty text="No open recommendations in this group for the current filter - applied ones do not repeat until the data changes." icon={<Sparkles size={26} />} /></Card>}
      </div>
      <div className="text-xs text-slate-400">Recommendations recompute on every data change and global filter change (AI-03). Applying an action such as a campaign preset opens the right section; employee moves are executed directly (BR-23 guarded).</div>
    </div>
  )
}
