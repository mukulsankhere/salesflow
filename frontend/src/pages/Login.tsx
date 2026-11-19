import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../lib/api'
import type { User } from '../lib/store'

const DEMO = [
  { email: 'admin@salesflow.app', label: 'Admin - Ravi Menon', desc: 'Single sign-in · full control' },
]

export default function Login() {
  const [email, setEmail] = useState('admin@salesflow.app')
  const [password, setPassword] = useState('Demo@123')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const nav = useNavigate()

  async function login(e?: React.FormEvent, demoEmail?: string) {
    e?.preventDefault()
    setBusy(true)
    setError('')
    try {
      const res = await api<{ access_token: string; user: User }>('/auth/login', {
        method: 'POST',
        body: { email_or_phone: demoEmail || email, password },
      })
      setToken(res.access_token)
      localStorage.setItem('sf_user', JSON.stringify(res.user))
      nav('/')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <div className="hidden lg:flex flex-col justify-between bg-brand-700 text-white p-10">
        <div>
          <div className="text-2xl font-extrabold tracking-tight">SalesFlow</div>
          <div className="text-brand-200 text-sm mt-1">Lead → Appointment → Renewal</div>
        </div>
        <div className="space-y-5 max-w-md">
          <h1 className="text-3xl font-bold leading-tight">Every portal lead booked. Every store neither idle nor overbooked.</h1>
          <ul className="text-brand-100 text-sm space-y-2">
            <li>• AI Calling: Aarav dials hot leads first, with SLA timers and instant human handoff</li>
            <li>• Automatic WhatsApp &amp; SMS: bookings, nudges and reminders go out on their own</li>
            <li>• One inbox: ingest, de-duplicate, score and route leads with SLA timers</li>
            <li>• Hour-level capacity, reroute at 90%, waitlists and the 10% far-location benefit</li>
            <li>• 30-day plans: day-25 renewals, held slots and staged win-back</li>
          </ul>
        </div>
        <div className="text-brand-200 text-xs">5 Stores · Indore · Skin, Hair, Fitness, Spa, Dental</div>
      </div>
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="lg:hidden text-2xl font-extrabold text-brand-700 mb-6">SalesFlow</div>
          <div className="card p-6">
            <h2 className="text-lg font-bold">Admin Sign In</h2>
            <p className="text-sm text-slate-500">Single admin account - employee sign-ins are disabled on this deployment.</p>
            <form onSubmit={login} className="mt-4 space-y-3">
              <div>
                <label className="label">Email Or Phone</label>
                <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div>
                <label className="label">Password</label>
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              {error && <div className="text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded-lg px-3 py-2">{error}</div>}
              <button className="btn-primary w-full justify-center py-2" disabled={busy}>
                {busy ? 'Signing In…' : 'Sign In'}
              </button>
            </form>
          </div>
          <div className="mt-4 space-y-2">
            {DEMO.map((d) => (
              <button key={d.email} onClick={() => login(undefined, d.email)} disabled={busy}
                className="w-full card px-4 py-2.5 flex items-center justify-between hover:border-brand-400 text-left">
                <div>
                  <div className="text-sm font-semibold">{d.label}</div>
                  <div className="text-xs text-slate-500">{d.desc}</div>
                </div>
                <div className="text-xs text-brand-600 font-medium">Demo@123 →</div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
