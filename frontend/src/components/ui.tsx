import { CSSProperties, ReactNode, useEffect } from 'react'
import { X, AlertTriangle, Inbox, CheckCircle2, Info } from 'lucide-react'
import { useToasts } from '../lib/toast'

export function Card({ children, className = '', onClick, style }: { children: ReactNode; className?: string; onClick?: () => void; style?: CSSProperties }) {
  return (
    <div className={`card ${className}`} onClick={onClick} style={style}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } } : undefined}>
      {children}
    </div>
  )
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
  // Escape closes the topmost behaviour the same way as the backdrop click;
  // the page behind a modal never scrolls (mobile bottom-sheet feel)
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', h); document.body.style.overflow = prev }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-start justify-center bg-slate-900/40 sm:p-4 anim-fade"
      onClick={onClose}>
      <div className={`bg-white shadow-xl w-full ${wide ? 'sm:max-w-4xl' : 'sm:max-w-lg'}
        rounded-t-2xl sm:rounded-xl sm:my-8 max-h-[88dvh] sm:max-h-[90vh] flex flex-col anim-sheet`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3 bg-white rounded-t-2xl sm:rounded-t-xl shrink-0">
          <h3 className="font-bold text-slate-800 pr-2">{title}</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 shrink-0" aria-label="Close"><X size={18} /></button>
        </div>
        <div className="p-4 sm:p-5 overflow-y-auto overscroll-contain">{children}</div>
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
      <div className={`h-full rounded-full transition-[width] duration-700 ease-out ${color || 'bg-brand-500'}`}
        style={{ width: `${Math.min(100, value)}%` }} />
      {target !== undefined && (
        <div className="absolute top-0 bottom-0 w-0.5 bg-slate-700 transition-[left] duration-700 ease-out"
          style={{ left: `${Math.min(100, target)}%` }} />
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

/** Shimmering placeholder lines while a list loads. */
export function Skeleton({ rows = 3, className = '' }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton h-9" style={{ opacity: 1 - i * 0.18 }} />
      ))}
    </div>
  )
}

/** Small inline spinner for buttons that are mid-request. */
export function BtnSpinner({ className = '' }: { className?: string }) {
  return <span className={`w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0 ${className}`} />
}

/** Global toast stack - rendered once at the app root. */
export function Toaster() {
  const { toasts, remove } = useToasts()
  return (
    <div className="fixed top-3 left-3 right-3 sm:left-auto sm:right-3 z-[100] flex flex-col items-end gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div key={t.id}
          className={`pointer-events-auto w-fit max-w-full flex items-start gap-2 rounded-lg border px-3 py-2 text-sm shadow-lg animate-[toast-in_.16s_ease-out]
            ${t.kind === 'success' ? 'bg-emerald-600 text-white border-emerald-700'
              : t.kind === 'error' ? 'bg-rose-600 text-white border-rose-700'
                : 'bg-slate-800 text-white border-slate-700'}`}>
          {t.kind === 'success' ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
            : t.kind === 'error' ? <AlertTriangle size={15} className="mt-0.5 shrink-0" />
              : <Info size={15} className="mt-0.5 shrink-0" />}
          <span className="max-w-[70vw] sm:max-w-xs">{t.text}</span>
          <button className="opacity-70 hover:opacity-100 ml-1 shrink-0" onClick={() => remove(t.id)}><X size={13} /></button>
        </div>
      ))}
    </div>
  )
}

/** Replacement for window.confirm - async-safe, styled, shows a busy state. */
export function ConfirmModal({ open, onClose, onConfirm, title, message, confirmLabel = 'Confirm', danger, busy }: {
  open: boolean; onClose: () => void; onConfirm: () => void
  title: string; message: string; confirmLabel?: string; danger?: boolean; busy?: boolean
}) {
  if (!open) return null
  return (
    <Modal open onClose={onClose} title={title}>
      <p className="text-sm text-slate-600">{message}</p>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className={danger ? 'btn-danger' : 'btn-primary'} disabled={busy} onClick={onConfirm}>
          {busy ? 'Working…' : confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
