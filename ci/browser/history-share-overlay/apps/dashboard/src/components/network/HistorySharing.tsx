import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { getHistoryShare, getHistoryShares, prepareHistoryShare, resolveHistoryShare, runHistoryShare,
  type HistoryShare, type HistoryShareAction, type HistoryShareDecision, type HistoryShareView } from '../../api/historyShares'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { allowNetworkNavigation, NETWORK_BUTTON, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination,
  NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

const views: HistoryShareView[] = ['received', 'sent']
const transport = {
  prepare: prepareHistoryShare,
  execute: runHistoryShare,
  resolve: resolveHistoryShare,
  canRetry: (intent: HistoryShareDecision, current: HistoryShare) =>
    current.version === intent.expected_version && current.actions.includes(intent.operation),
}

function useExpired(expiresAt?: string) {
  const [, setTick] = useState(0)
  const delay = expiresAt ? Date.parse(expiresAt) - Date.now() : undefined
  useEffect(() => {
    if (delay === undefined || delay <= 0) return
    const timer = window.setTimeout(() => setTick(tick => tick + 1), Math.min(delay, 2147483647))
    return () => window.clearTimeout(timer)
  }, [delay])
  return delay !== undefined && delay <= 0
}

export function HistorySharing({ actorId, initialView, options }: { actorId: string; initialView: HistoryShareView; options: ProfessionalOptions }) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [view, setView] = useState(initialView), [page, setPage] = useState(1), [active, setActive] = useState('')
  const [completed, setCompleted] = useState(false)
  const read = useNetworkRead(`history-shares:${actorId}:${view}:${page}`, signal => getHistoryShares(view, page, signal), !active)
  const data = !read.stale && !read.error && !(view === 'received' && read.checking) ? read.data : null
  function choose(next: HistoryShareView) {
    if (view === next || !allowNetworkNavigation()) return
    setView(next); setPage(1); setCompleted(false)
  }
  if (active) return <HistoryShareDetail key={active} actorId={actorId} view={view} id={active} options={options}
    onBack={applied => { setActive(''); setCompleted(applied) }} />
  return <NetworkPanel title={t('history_shares.title')} headingId={heading}>
    <nav aria-label={t('history_shares.views')} className="mb-4 flex flex-wrap gap-2">
      {views.map(value => <button key={value} type="button" className={view === value ? NETWORK_PRIMARY : NETWORK_BUTTON}
        aria-current={view === value ? 'page' : undefined} onClick={() => choose(value)}>{t(`history_shares.${value}`)}</button>)}
    </nav>
    <NetworkFreshness read={read} />
    {completed && <p role="status" className="mb-3 text-sm">{t('service.action_saved')}</p>}
    {!!read.error && <p role="alert" className="text-sm text-red-800">{t(networkError(read.error))}</p>}
    {data && <>
      {data.items.length > 0 ? <ul className="space-y-3">{data.items.map(item =>
        <li key={item.id} className="min-w-0 rounded-xl border border-line p-3">
          <h3 className="break-words font-semibold">{item.label}</h3>
          <ShareMetadata share={item} />
          <button type="button" className={`${NETWORK_BUTTON} mt-2`} disabled={read.checking}
            onClick={() => { setActive(item.id); setCompleted(false) }}>{t('history_shares.open')}</button>
        </li>)}</ul> : <p role="status" className="text-sm">{t(`history_shares.empty_${view}`)}</p>}
      <NetworkPagination page={page} total={data.total} busy={read.checking} onChange={setPage} />
    </>}
  </NetworkPanel>
}

function ShareMetadata({ share }: { share: HistoryShare }) {
  const { t } = useTranslation('property_network')
  return <div className="mt-2 space-y-1 text-sm text-dim">
    <p>{t(`history_shares.statuses.${share.status}`)} · {t('history_shares.entry_count', { count: share.item_count })}</p>
    <p>{t('history_shares.expires')} <NetworkDate value={share.expires_at} showTime /></p>
  </div>
}

export function HistoryWorkMetadata({ date, ids, categories }: {
  date: string; ids: string[]; categories: ProfessionalOptions['categories']
}) {
  const { i18n } = useTranslation('property_network')
  return <><NetworkDate value={date} />{ids.map(id => {
    const category = categories.find(option => option.id === id)
    return <span key={id}> · {category ? professionalLabel(category.label, i18n.language) : id}</span>
  })}</>
}

function HistoryShareDetail({ actorId, id, view, options, onBack }: {
  actorId: string; id: string; view: HistoryShareView; options: ProfessionalOptions; onBack: (applied: boolean) => void
}) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [confirmation, setConfirmation] = useState<HistoryShareAction | null>(null)
  const [minimumVersion, setMinimumVersion] = useState(0)
  const load = (signal: AbortSignal) => getHistoryShare(id, view, signal)
  const read = useNetworkRead(`history-share:${actorId}:${view}:${id}`, load)
  const accessLost = !!read.error && networkError(read.error) === 'error_access'
  const expired = useExpired(read.data?.expires_at)
  const mutation = useServiceAttempt(transport, load, result => {
    setConfirmation(null)
    if (view === 'received' && result.operation !== 'accept') { onBack(true); return }
    setMinimumVersion(result.version)
    read.refresh()
  }, () => { setConfirmation(null); read.refresh() }, accessLost || (view === 'received' && expired))
  const locked = mutation.pending !== null || mutation.busy
  const share = read.data && !read.stale && !read.error && !mutation.denied && !accessLost
    && !(view === 'received' && (expired || read.checking)) && read.data.version >= minimumVersion ? read.data : null
  const actionReady = !!share && !read.checking && !locked && !expired
  useNetworkDraftGuard(locked && !mutation.denied, mutation.busy)
  useEffect(() => { if (accessLost || (expired && view === 'received')) setConfirmation(null) }, [accessLost, expired, view])

  function confirm() {
    if (!share || !actionReady || !confirmation || !share.actions.includes(confirmation)) return
    mutation.send({ operation: confirmation, id, expected_version: share.version, submission_id: crypto.randomUUID() })
    setConfirmation(null)
  }

  return <NetworkPanel title={t('history_shares.detail')} headingId={heading}>
    <button type="button" className={`${NETWORK_BUTTON} mb-3`} onClick={() => { if (allowNetworkNavigation()) onBack(false) }}>{t('history_shares.back')}</button>
    {!accessLost && !mutation.denied && <NetworkFreshness read={read} />}
    {view === 'received' && expired ? <p role="status">{t('history_shares.expired_notice')}</p>
      : !!read.error && <p role="alert" className="text-sm text-red-800">{t(networkError(read.error))}</p>}
    {share && !locked && <div className="space-y-4">
      <div><h3 className="break-words font-bold">{share.label}</h3><ShareMetadata share={share} /></div>
      {view === 'received' && share.status === 'pending' && <p className="text-sm">{t('history_shares.invitation_help')}</p>}
      {share.items && <section aria-label={t('history_shares.summaries')}>
        <h4 className="mb-2 font-semibold">{t('history_shares.summaries')}</h4>
        <ol className="space-y-2">{share.items.map((item, index) => <li key={index} className="min-w-0 rounded-xl border border-line p-3">
          <p className="mb-2 text-sm text-dim"><HistoryWorkMetadata
            date={item.occurred_on} ids={item.service_category_ids} categories={options.categories} /></p>
          <p className="whitespace-pre-wrap break-words text-sm">{item.summary}</p>
        </li>)}</ol>
      </section>}
      {confirmation && share.actions.includes(confirmation) ? <div className="space-y-3 rounded-xl border border-line p-3">
        <p className="text-sm">{t(`history_shares.confirmations.${confirmation}`)}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={NETWORK_PRIMARY} disabled={!actionReady} onClick={confirm}>{t('history_shares.confirm_action')}</button>
          <button type="button" className={NETWORK_BUTTON} onClick={() => setConfirmation(null)}>{t('history_shares.cancel')}</button>
        </div>
      </div> : <div className="flex flex-wrap gap-2">
        {share.actions.map(action => <button type="button" className={NETWORK_BUTTON} key={action} disabled={!actionReady}
          onClick={() => setConfirmation(action)}>{t(`history_shares.actions.${action}`)}</button>)}
      </div>}
    </div>}
    <ServiceMutationStatus mutation={{ ...mutation, saved: mutation.saved && share !== null }} />
  </NetworkPanel>
}
