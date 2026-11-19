import { Navigate, Route, Routes } from 'react-router-dom'
import { getToken } from './lib/api'
import { useFilter } from './lib/store'
import { Toaster } from './components/ui'
import Login from './pages/Login'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import AICalls from './pages/AICalls'
import Leads from './pages/Leads'
import Customers from './pages/Customers'
import Marketing from './pages/Marketing'
import Appointments from './pages/Appointments'
import Team from './pages/Team'
import Services from './pages/Services'
import Settings from './pages/Settings'

function RequireAuth({ children, section }: { children: JSX.Element; section?: string }) {
  const userStr = localStorage.getItem('sf_user')
  if (!getToken() || !userStr) return <Navigate to="/login" replace />
  const user = JSON.parse(userStr)
  if (section) {
    const allowed: Record<string, string[]> = {
      dashboard: ['admin'],
      aicalls: ['admin'],
      leads: ['admin'],
      customers: ['admin'],
      marketing: ['admin'],
      appointments: ['admin'],
      team: ['admin'],
      services: ['admin'],
      settings: ['admin'],
    }
    const role = user.roles?.[0]
    if (section !== 'dashboard' && allowed[section] && !allowed[section].includes(role)) {
      return (
        <div className="p-10 text-center">
          <div className="card max-w-md mx-auto p-8">
            <div className="text-3xl mb-2">🔒</div>
            <h2 className="text-lg font-bold">Section Not Available</h2>
            <p className="text-sm text-slate-500 mt-1">
              Your role ({role}) does not have access to {section}. Ask an Admin for more permissions.
            </p>
          </div>
        </div>
      )
    }
  }
  return children
}

export default function App() {
  useFilter((s) => s.period) // keep filter store hydrated
  return (
    <>
      <Routes>
        <Route path="/login" element={<Login />} />
      <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<RequireAuth section="dashboard"><Dashboard /></RequireAuth>} />
        <Route path="ai-calls" element={<RequireAuth section="aicalls"><AICalls /></RequireAuth>} />
        <Route path="leads" element={<RequireAuth section="leads"><Leads /></RequireAuth>} />
        <Route path="customers" element={<RequireAuth section="customers"><Customers /></RequireAuth>} />
        <Route path="marketing" element={<RequireAuth section="marketing"><Marketing /></RequireAuth>} />
        <Route path="appointments" element={<RequireAuth section="appointments"><Appointments /></RequireAuth>} />
        <Route path="team" element={<RequireAuth section="team"><Team /></RequireAuth>} />
        <Route path="services" element={<RequireAuth section="services"><Services /></RequireAuth>} />
        <Route path="settings" element={<RequireAuth section="settings"><Settings /></RequireAuth>} />
        {/* the AI now works the queue: Action Queue became AI Calling */}
        <Route path="queue" element={<Navigate to="/ai-calls" replace />} />
        <Route path="quick" element={<Navigate to="/ai-calls" replace />} />
        {/* Products tab was renamed to Services */}
        <Route path="products" element={<Navigate to="/services" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  )
}
