import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { createNetworkReadLoop, type NetworkReadLoop, type NetworkReadState } from '../lib/networkFreshness'

export type NetworkRead<T> = NetworkReadState & { data: T | null; error: unknown; refresh: () => void }
type ReadSnapshot<T> = Omit<NetworkRead<T>, 'refresh'> & { key: string }
const empty = <T,>(key: string): ReadSnapshot<T> => ({ key, data: null, error: null, checking: false, checkedAt: null, stale: true })

/** Context keys include every request ID/page. Background reads preserve forms. */
export function useNetworkRead<T>(key: string, load: (signal: AbortSignal) => Promise<T>, enabled = true): NetworkRead<T> {
  const [snapshot, setSnapshot] = useState<ReadSnapshot<T>>(() => empty(key))
  const active = useRef<NetworkReadLoop | null>(null)
  useEffect(() => {
    setSnapshot(empty(key))
    if (!enabled) return
    const update = (change: Partial<ReadSnapshot<T>>) => setSnapshot(previous => previous.key === key ? { ...previous, ...change } : previous)
    const loop = createNetworkReadLoop({
      load, visible: () => document.visibilityState !== 'hidden', online: () => navigator.onLine !== false,
      now: Date.now, setTimer: (callback, delay) => window.setTimeout(callback, delay), clearTimer: timer => window.clearTimeout(timer as number),
      onData: data => update({ data, error: null }),
      onError: error => {
        const denied = isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0)
        update({ error, ...(denied ? { data: null } : {}) })
        return denied
      },
      onState: update,
    })
    active.current = loop
    const visibility = () => { if (document.visibilityState === 'hidden') loop.pause(); else loop.refresh() }
    const wake = () => loop.refresh(), pause = () => loop.pause()
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('online', wake); window.addEventListener('offline', pause); window.addEventListener('focus', wake)
    return () => {
      loop.dispose(); if (active.current === loop) active.current = null
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('online', wake); window.removeEventListener('offline', pause); window.removeEventListener('focus', wake)
    }
    // The key is the complete request context; inline loaders are not dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled])
  const current = snapshot.key === key && enabled ? snapshot : empty<T>(key)
  return { ...current, refresh: () => active.current?.refresh() }
}
