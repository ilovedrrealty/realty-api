export type NetworkReadState = { checking: boolean; checkedAt: number | null; stale: boolean }
export type NetworkReadLoop = { refresh: () => void; pause: () => void; dispose: () => void }

/** Read-only, one-request loop. Timers and presence are injected for bounded tests. */
export function createNetworkReadLoop<T>(options: {
  load: (signal: AbortSignal) => Promise<T>
  visible: () => boolean; online: () => boolean; now: () => number
  setTimer: (callback: () => void, delay: number) => unknown; clearTimer: (timer: unknown) => void
  onData: (value: T) => void; onError: (error: unknown) => boolean
  onState: (state: NetworkReadState) => void
}): NetworkReadLoop {
  let disposed = false, stopped = false, queued = false, inFlight = false
  let interval: unknown, deadline: unknown, controller: AbortController | null = null
  let state: NetworkReadState = { checking: false, checkedAt: null, stale: true }
  const eligible = () => !disposed && !stopped && options.visible() && options.online()
  const emit = (change: Partial<NetworkReadState>) => { state = { ...state, ...change }; if (!disposed) options.onState({ ...state }) }
  const clearInterval = () => { if (interval !== undefined) options.clearTimer(interval); interval = undefined }
  function refresh() {
    clearInterval()
    if (disposed || stopped) return
    if (!eligible()) { emit({ stale: true }); return }
    if (inFlight) { queued = true; return }
    inFlight = true; queued = false
    const request = new AbortController(); controller = request
    let timedOut = false
    emit({ checking: true })
    deadline = options.setTimer(() => {
      timedOut = true; request.abort()
      emit({ stale: true, checking: false })
    }, 15000)
    Promise.resolve().then(() => {
      if (request.signal.aborted) throw new Error('Network read cancelled')
      return options.load(request.signal)
    }).then(value => {
      if (request.signal.aborted || !eligible()) return
      options.onData(value); emit({ checkedAt: options.now(), stale: false })
    }).catch(error => {
      if (disposed || (request.signal.aborted && !timedOut)) return
      stopped = options.onError(error); emit({ stale: true })
    }).finally(() => {
      if (deadline !== undefined) options.clearTimer(deadline)
      deadline = undefined; controller = null; inFlight = false
      if (disposed) return
      emit({ checking: false })
      if (!eligible()) return
      if (queued) { queued = false; refresh() }
      else interval = options.setTimer(refresh, 30000)
    })
  }
  function pause() {
    clearInterval(); queued = false; controller?.abort(); emit({ stale: true, checking: false })
    if (deadline !== undefined) options.clearTimer(deadline)
    deadline = undefined
  }
  function dispose() {
    disposed = true; clearInterval(); queued = false; controller?.abort()
    if (deadline !== undefined) options.clearTimer(deadline)
  }
  refresh()
  return { refresh, pause, dispose }
}
