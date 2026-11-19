import { useEffect, useRef, useState } from 'react'

/** Animate a number from its previous value to `target` with an ease-out count-up.
 *  On first load it counts up from 0; skips motion when reduced-motion is set. */
export function useCountUp(target: number | undefined, duration = 650) {
  const [value, setValue] = useState(0)
  const from = useRef(0)

  useEffect(() => {
    if (target === undefined) return
    const a = from.current
    if (a === target) { setValue(target); return }
    if (!Number.isFinite(target)) { setValue(target); from.current = target; return }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setValue(target); from.current = target; return
    }
    let raf = 0
    const start = performance.now()
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      const v = Math.round(a + (target - a) * eased)
      setValue(v)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])

  return value
}
