import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Role = 'admin' | 'manager' | 'sales' | 'staff' | 'marketing' | 'readonly'

export interface User {
  _id: string
  name: string
  email: string
  roles: Role[]
  store_ids: string[]
}

interface FilterState {
  period: 'today' | '7' | '30' | '90' | 'all'
  storeId: string | '' // '' = all stores
  setPeriod: (p: FilterState['period']) => void
  setStore: (s: string) => void
}

export const useFilter = create<FilterState>()(
  persist(
    (set) => ({
      period: 'all',
      storeId: '', // '' = all stores
      setPeriod: (period) => set({ period }),
      setStore: (storeId) => set({ storeId }),
    }),
    // version 1 discards older persisted filters (pre-'all'-default) so the
    // All Time / All Stores default actually applies to returning visitors
    { name: 'sf_filter', version: 1 },
  ),
)

/** Query param pair from the global filter. */
export function filterParams(f: FilterState): Record<string, string> {
  const out: Record<string, string> = { period: f.period }
  if (f.storeId) out.store_id = f.storeId
  return out
}

interface FeedEvent {
  id: number
  type: string
  payload: Record<string, unknown>
  at: string
}

interface RtState {
  events: FeedEvent[]
  connected: boolean
  push: (e: FeedEvent) => void
  setConnected: (c: boolean) => void
  clear: () => void
}

let nextId = 1

export const useRt = create<RtState>()((set) => ({
  events: [],
  connected: false,
  push: (e) =>
    set((s) => ({ events: [{ ...e, id: nextId++ }, ...s.events].slice(0, 60) })),
  setConnected: (connected) => set({ connected }),
  clear: () => set({ events: [] }),
}))

export type { FeedEvent }

export function feedText(e: FeedEvent): string {
  const p = e.payload || {}
  if (typeof p.text === 'string' && p.text) return p.text
  switch (e.type) {
    case 'lead.created':
      return `New lead ${p.name} (${p.service}, ${p.store}) - score ${p.score}`
    case 'interaction.logged':
      return `Message logged for ${p.person} via ${p.channel}`
    case 'reply.received':
      return `Reply received: "${p.text}"`
    case 'appointment.booked':
      return `${p.person} booked ${p.service} at ${p.store} for ${p.date} ${p.hour}:00`
    case 'appointment.cancelled':
      return `Appointment cancelled`
    case 'appointment.rescheduled':
      return `Appointment rescheduled`
    case 'appointment.no_show':
      return `No-show marked`
    case 'visit.completed':
      return `Visit completed: ${p.person} - ${p.service} at ${p.store}`
    case 'followup.due':
      return `Follow-up due: ${p.person}`
    case 'waitlist.offered':
      return `Waitlist offer sent for ${p.date} ${p.hour}:00`
    case 'waitlist.slots_open':
      return `A slot opened - waitlist notified`
    case 'campaign.status':
      return `Campaign "${p.name || p.campaign_id}" is now ${p.status}`
    case 'campaign.reply':
      return `Campaign reply: "${p.text}"`
    case 'ai_call.started':
      return `AI dialing ${p.person} (score ${p.score})`
    case 'ai_call.outcome':
      return p.outcome === 'taken_over' ? `You took over the call with ${p.person}`
        : `AI call to ${p.person}: ${String(p.outcome).replace(/_/g, ' ')}`
    case 'ai_call.shift':
      return `AI shift ${p.enabled ? 'started' : 'paused'} by ${p.by}`
    case 'ai_message.sent':
      return `${String(p.channel).toUpperCase()} sent to ${p.person}`
    case 'ai_message.reply':
      return `Reply from ${p.person}: "${p.text}"`
    default:
      return e.type.replace(/[._]/g, ' ')
  }
}
