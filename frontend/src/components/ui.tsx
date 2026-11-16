import { ReactNode } from 'react'
import { X, AlertTriangle, Inbox } from 'lucide-react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>
}

export function SectionTitle({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="flex items-end justify-between mb-3">
      <div>
        <h2 className="text-base font-bold text-slate-800">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </div>
      {right}
    </div>
  )
}

export function Modal({ open, onClose, title, children, wide }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4" onClick={onClose}>
      <div className={`bg-white rounded-xl shadow-xl w-full ${wide ? 'max-w-4xl' : 'max-w-lg'} my-8`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3 sticky top-0 bg-white rounded-t-xl z-10">
          <h3 className="font-bold text-slate-800">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-slate-100"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function Tabs({ tabs, active, onChange }: { tabs: { key: string; label: string; count?: number }[]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-slate-200 mb-4">
      {tabs.map((t) => (
        <button key={t.key} onClick={() => onChange(t.key)}
          className={`px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition
            ${active === t.key ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
          {t.label}
          {t.count !== undefined && (
            <span className={`ml-1.5 px-1.5 py-0.5 rounded-full text-[11px] font-semibold
              ${active === t.key ? 'bg-brand-100 text-brand-700' : 'bg-slate-100 text-slate-500'}`}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  )
}

export function Empty({ text, icon }: { text: string; icon?: ReactNode }) {
  return (
    <div className="text-center py-10 text-slate-400">
      <div className="flex justify-center mb-2">{icon || <Inbox size={28} />}</div>
      <div className="text-sm">{text}</div>
    </div>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="flex items-start gap-2 bg-rose-50 border border-rose-200 text-rose-700 rounded-lg px-3 py-2 text-sm">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" />
      <span>{(error as Error)?.message || 'Something went wrong'}</span>
    </div>
  )
}

export function Bar({ value, target, color }: { value: number; target?: number; color?: string }) {
  return (
    <div className="relative h-2.5 bg-slate-100 rounded-full overflow-hidden">
      <div className={`h-full rounded-full ${color || 'bg-brand-500'}`} style={{ width: `${Math.min(100, value)}%` }} />
      {target !== undefined && (
        <div className="absolute top-0 bottom-0 w-0.5 bg-slate-700" style={{ left: `${Math.min(100, target)}%` }} />
      )}
    </div>
  )
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-slate-50 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-800 text-right">{children}</span>
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex justify-center py-12">
      <div className="w-6 h-6 border-2 border-brand-200 border-t-brand-600 rounded-full animate-spin" />
    </div>
  )
}
