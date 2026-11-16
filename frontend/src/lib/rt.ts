import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useRt, type FeedEvent } from './store'
import { getToken } from './api'

/** RT-01/RT-02: Server-Sent Events stream; refreshes affected queries live. */
export function useRealtime() {
  const qc = useQueryClient()
  const push = useRt((s) => s.push)
  const setConnected = useRt((s) => s.setConnected)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!getToken()) return
    const es = new EventSource(`/api/v1/events/stream?token=${getToken()}`)
    esRef.current = es
    const handle = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as FeedEvent
        push({ id: 0, type: ev.type, payload: data.payload || data, at: data.at || new Date().toISOString() })
      } catch {
        /* ignore */
      }
    }
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    for (const t of ['lead.created', 'reply.received', 'appointment.booked', 'appointment.cancelled',
      'appointment.rescheduled', 'appointment.no_show', 'visit.completed', 'followup.due',
      'waitlist.offered', 'waitlist.slots_open', 'campaign.status', 'campaign.reply', 'interaction.logged']) {
      es.addEventListener(t, handle as EventListener)
    }
    return () => {
      es.close()
      setConnected(false)
    }
  }, [push, setConnected])

  const lastId = useRef(0)
  useEffect(() => {
    const unsub = useRt.subscribe((s) => {
      const first = s.events[0]
      if (first && first.id !== lastId.current) {
        lastId.current = first.id
        void qc.invalidateQueries()
      }
    })
    return unsub
  }, [qc])
}
