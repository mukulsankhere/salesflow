import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useQuery } from '@tanstack/react-query'
import { Sparkles, ArrowUpRight, X, Check, TrendingUp, IndianRupee } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { toast } from '../lib/toast'
import { useFilter, filterParams } from '../lib/store'
import { fmtMoney } from '../lib/format'
import { Card, Tabs, Empty, Spinner, SectionTitle, BtnSpinner } from '../components/ui'

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
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      toast.success('Recommendation applied - it leaves the list and takes effect immediately')
      void v
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const dismiss = useMutation({
    mutationFn: ({ scope, key }: { scope: string; key: string }) => api(`/insights/${scope}/${key}/dismiss`, { method: 'POST' }),
    onSuccess: () => { void qc.invalidateQueries(); toast.success('Recommendation dismissed - it stays hidden until the data changes') },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading) return <Spinner />
  const recs = data?.tabs[tab] || []
  const impact = data?.impact

  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        <b>Rule-based insights.</b> Every recommendation below is computed from fixed rules over your live data
        (segment sizes, response times, utilisation) - no AI model is involved, and the figures are estimates from
        recent conversion rates.
      </div>
      {/* impact summary */}
      <div className="grid grid-cols-3 gap-2 sm:gap-3">
        <Card className="p-3 sm:p-4 flex items-center gap-2 sm:gap-3 min-w-0">
          <TrendingUp className="text-emerald-600 shrink-0" size={18} />
          <div className="min-w-0"><div className="text-base sm:text-xl font-extrabold text-emerald-600 tabular-nums">+{impact?.expected_bookings}</div><div className="text-[10px] sm:text-[11px] text-slate-400">Expected Bookings</div></div>
        </Card>
        <Card className="p-3 sm:p-4 flex items-center gap-2 sm:gap-3 min-w-0">
          <IndianRupee className="text-teal-600 shrink-0" size={18} />
          <div className="min-w-0"><div className="text-base sm:text-xl font-extrabold text-teal-700 tabular-nums truncate">{fmtMoney(impact?.expected_revenue || 0)}</div><div className="text-[10px] sm:text-[11px] text-slate-400">Estimated Revenue</div></div>
        </Card>
        <Card className="p-3 sm:p-4 flex items-center gap-2 sm:gap-3 min-w-0">
          <Sparkles className="text-brand-600 shrink-0" size={18} />
          <div className="min-w-0"><div className="text-base sm:text-xl font-extrabold tabular-nums">{impact?.open}</div><div className="text-[10px] sm:text-[11px] text-slate-400">Open Recommendations</div></div>
        </Card>
      </div>

      <Tabs active={tab} onChange={setTab} tabs={TABS.map((t) => ({ key: t.key, label: t.label, count: data?.tabs[t.key]?.length }))} />

      <div className="space-y-3">
        {recs.map((r) => (
          <Card key={r.key} className="p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="font-bold text-slate-800">{r.title}</div>
                <div className="text-sm text-slate-600 mt-1">{r.text}</div>
                <div className="flex flex-wrap gap-2 mt-2 text-xs">
                  {Object.entries(r.evidence).map(([k, v]) => (
                    <span key={k} className="chip bg-slate-50 border-slate-200 text-slate-500">{k.replace(/_/g, ' ')}: {String(v)}</span>
                  ))}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5 text-sm font-semibold">
                  <span className="text-emerald-600">+{r.expected_bookings} Expected Bookings</span>
                  <span className="text-teal-700">{fmtMoney(r.expected_revenue)} Expected Revenue</span>
                </div>
              </div>
              <div className="flex flex-col items-end gap-2 shrink-0 max-w-[46%]">
                <button className="btn-primary btn-sm whitespace-normal text-right" disabled={apply.isPending && apply.variables?.key === r.key}
                  onClick={() => apply.mutate({ scope: data!.scope, key: r.key })}>
                  {apply.isPending && apply.variables?.key === r.key ? <BtnSpinner /> : <Check size={12} />} {r.action.label}
                </button>
                <button className="btn-ghost btn-sm text-slate-400 whitespace-normal text-right" disabled={dismiss.isPending && dismiss.variables?.key === r.key}
                  onClick={() => dismiss.mutate({ scope: data!.scope, key: r.key })}>
                  {dismiss.isPending && dismiss.variables?.key === r.key ? <BtnSpinner /> : <X size={12} />} Dismiss
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
