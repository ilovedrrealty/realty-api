import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getServiceFavorites, removeServiceFavorite, SERVICE_FAVORITE_PAGE_SIZE } from '../../api/serviceFavorites'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import {
  NETWORK_BUTTON, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard,
} from './NetworkShared'

/** Favorite changes are explicit and independent of service-request drafts. */
export function useFavoriteChange() {
  const pending = useRef<AbortController | null>(null)
  const [state, setState] = useState({ busy: false, saved: false, error: '' })
  useEffect(() => () => pending.current?.abort(), [])

  async function run(change: (signal: AbortSignal) => Promise<unknown>, onSaved: () => void, onError?: (code: string) => void) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setState({ busy: true, saved: false, error: '' })
    try {
      await change(controller.signal)
      if (controller.signal.aborted) return
      onSaved()
      setState({ busy: false, saved: true, error: '' })
    } catch (error) {
      if (controller.signal.aborted) return
      const code = networkError(error)
      onError?.(code)
      setState({ busy: false, saved: false, error: code })
    } finally {
      if (pending.current === controller) pending.current = null
    }
  }

  return { ...state, run }
}

export function FavoriteChangeStatus({ change, success }: { change: ReturnType<typeof useFavoriteChange>; success: string }) {
  const { t } = useTranslation('property_network')
  if (change.error) return <p role="alert" className="text-sm text-red-800">{t(change.error)}</p>
  if (change.saved) return <p role="status" className="text-sm text-ink2">{t(success)}</p>
  return null
}

export function ServiceFavorites({ actorId }: { actorId: string }) {
  const { t } = useTranslation('property_network')
  const titleId = useId()
  const [page, setPage] = useState(1)
  const [removedId, setRemovedId] = useState('')
  const read = useNetworkRead(`service-favorites:${actorId}:${page}`, signal => getServiceFavorites(page, signal))
  const change = useFavoriteChange()
  const error = read.error ? networkError(read.error) : ''
  const fresh = !!read.data && !read.stale && !read.checking && !read.error
  const items = error === 'error_access' ? [] : (read.data?.items ?? []).filter(item => item.id !== removedId)
  useNetworkDraftGuard(false, change.busy)

  function remove(id: string) {
    if (!fresh || change.busy || !items.some(item => item.id === id)) return
    void change.run(signal => removeServiceFavorite(id, signal), () => {
      setRemovedId(id)
      if (page > 1 && items.length === 1) setPage(page - 1)
      else read.refresh()
    }, () => read.refresh())
  }

  return <NetworkPanel title={t('favorites.title')} headingId={titleId}>
    <NetworkFreshness read={read} />
    {error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}
    {items.length ? <ul className="space-y-3">
      {items.map(item => <li key={item.id} className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-white p-3">
        <div className="min-w-0 text-sm">
          <p className="break-words font-semibold text-ink">{item.provider?.member_alias ?? t('favorites.unavailable')}</p>
          {item.provider ? <p className="text-dim">{t('service.member_code', { code: item.provider.user_code })}</p> : null}
          <p className="text-xs text-dim"><NetworkDate value={item.saved_at} /></p>
        </div>
        <button type="button" className={NETWORK_BUTTON} disabled={!fresh || change.busy} onClick={() => remove(item.id)}>{t('favorites.remove')}</button>
      </li>)}
    </ul> : read.data && !error && !read.checking ? <p role="status" className="text-sm text-dim">{t('favorites.empty')}</p>
      : !read.data && !error ? <p role="status">{t('loading')}</p> : null}
    {read.data && error !== 'error_access' && (read.data.total > SERVICE_FAVORITE_PAGE_SIZE || page > 1) ? <NetworkPagination
      page={page} total={read.data.total} busy={read.checking || change.busy} onChange={setPage} /> : null}
    <FavoriteChangeStatus change={change} success="favorites.removed" />
  </NetworkPanel>
}
