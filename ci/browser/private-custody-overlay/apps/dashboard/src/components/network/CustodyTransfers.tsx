import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ProfessionalOptions } from '../../../../../packages/professionals/src'
import { getCustodyTransfer, getCustodyTransfers, prepareCustodyTransfer, resolveCustodyTransfer, runCustodyTransfer,
  type CustodyAction, type CustodyDecision, type CustodyTransfer, type CustodyView } from '../../api/registryCustody'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { allowNetworkNavigation, NETWORK_BUTTON, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination,
  NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'
import { CustodyPropertyFields } from './CustodyTransferOffer'

const views: CustodyView[] = ['received', 'sent']
const actionTransport = {
  prepare: prepareCustodyTransfer,
  execute: runCustodyTransfer,
  resolve: resolveCustodyTransfer,
  canRetry: (intent: CustodyDecision, current: CustodyTransfer) => current.version === intent.expected_version
    && current.actions.includes(intent.operation) && Date.parse(current.expires_at) > Date.now(),
}

type ListProps = { actorId: string; initialView: CustodyView; classes: ProfessionalOptions['property_classes']; onProperty: (id: string) => void }
export function CustodyTransfers({ actorId, initialView, classes, onProperty }: ListProps) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [view, setView] = useState(initialView), [page, setPage] = useState(1), [selected, setSelected] = useState('')
  const read = useNetworkRead(`registry-custody:${actorId}:${view}:${page}`, signal => getCustodyTransfers(view, page, signal), !selected)
  const data = !read.stale && !read.error && !read.checking ? read.data : null
  function choose(next: CustodyView) {
    if (next === view || !allowNetworkNavigation()) return
    setView(next)
    setPage(1)
  }
  if (selected) return <CustodyTransferDetail key={selected} id={selected} view={view} actorId={actorId}
    classes={classes} onProperty={onProperty} onBack={() => setSelected('')} />
  return <NetworkPanel title={t('custody.title')} headingId={heading}>
    <nav className="mb-4 flex flex-wrap gap-2" aria-label={t('custody.views')}>
      {views.map(value => <button key={value} type="button" className={value === view ? NETWORK_PRIMARY : NETWORK_BUTTON}
        aria-current={value === view ? 'page' : undefined} onClick={() => choose(value)}>{t(`custody.${value}`)}</button>)}
    </nav>
    <NetworkFreshness read={read} />
    {read.error ? <p role="alert" className="text-sm text-red-800">{t(networkError(read.error))}</p> : null}
    {data && <>
      {data.items.length ? <ul className="space-y-3">{data.items.map(row => <li key={row.id} className="min-w-0 rounded-xl border border-line p-3">
        <h3 className="break-words font-bold">{row.label}</h3>
        <TransferStatus transfer={row} />
        <button type="button" className={`${NETWORK_BUTTON} mt-2`} onClick={() => setSelected(row.id)}>{t('custody.open')}</button>
      </li>)}</ul> : <p role="status" className="text-sm">{t(`custody.empty_${view}`)}</p>}
      <NetworkPagination page={page} busy={read.checking} total={data.total} onChange={setPage} />
    </>}
  </NetworkPanel>
}

function TransferStatus({ transfer }: { transfer: CustodyTransfer }) {
  const { t } = useTranslation('property_network')
  return <div className="mt-1 space-y-1 text-sm text-dim">
    <p>{t(`custody.statuses.${transfer.status}`)}</p>
    {transfer.status === 'pending' ? <p>{t('custody.expires')} <NetworkDate value={transfer.expires_at} showTime /></p> : null}
  </div>
}
type DetailProps = Pick<ListProps, 'actorId' | 'classes' | 'onProperty'> & { id: string; view: CustodyView; onBack: () => void }
function CustodyTransferDetail({ id, view, actorId, classes, onBack, onProperty }: DetailProps) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [confirmation, setConfirmation] = useState<CustodyAction | null>(null)
  const [acceptReviewed, setAcceptReviewed] = useState(false)
  const [minimumVersion, setMinimumVersion] = useState(0)
  const [elapsed, setElapsed] = useState(false)
  const load = (signal: AbortSignal) => getCustodyTransfer(id, view, signal)
  const read = useNetworkRead(`registry-custody:${actorId}:${view}:${id}`, load)
  const accessLost = !!read.error && networkError(read.error) === 'error_access'
  const mutation = useServiceAttempt(actionTransport, load, receipt => {
    setMinimumVersion(receipt.version)
    setConfirmation(null)
    setAcceptReviewed(false)
    read.refresh()
  }, () => {
    setConfirmation(null)
    setAcceptReviewed(false)
    read.refresh()
  }, accessLost)
  const locked = mutation.pending !== null || mutation.busy
  const transfer = !read.stale && !read.checking && !read.error && !mutation.denied && read.data
    && read.data.version >= minimumVersion ? read.data : null
  const expiredPending = transfer?.status === 'pending' && (elapsed || Date.parse(transfer.expires_at) <= Date.now())
  const deadline = read.data?.status === 'pending' ? read.data.expires_at : null
  useNetworkDraftGuard(locked && !mutation.denied, mutation.busy)
  useEffect(() => {
    if (accessLost || expiredPending) { setConfirmation(null); setAcceptReviewed(false) }
  }, [accessLost, expiredPending])
  useEffect(() => {
    if (!deadline) return
    const remaining = Date.parse(deadline) - Date.now()
    if (remaining <= 0) { setElapsed(true); return }
    const timer = window.setTimeout(() => { setElapsed(true); read.refresh() }, remaining)
    return () => window.clearTimeout(timer)
  }, [deadline])

  function confirm(action: CustodyAction) {
    if (!transfer || locked || expiredPending || confirmation !== action || !transfer.actions.includes(action)
      || (action === 'accept' && !acceptReviewed)) return
    mutation.send({ operation: action, id, expected_version: transfer.version, submission_id: crypto.randomUUID(),
      ...(action === 'accept' ? { transfer_acknowledged: true } : {}) })
    setConfirmation(null)
    setAcceptReviewed(false)
  }

  return <NetworkPanel title={t('custody.detail')} headingId={heading}>
    <button type="button" className={`${NETWORK_BUTTON} mb-3`} onClick={() => { if (allowNetworkNavigation()) onBack() }}>{t('custody.back')}</button>
    {!accessLost && !mutation.denied && <NetworkFreshness read={read} />}
    {read.error ? <p role="alert" className="text-sm text-red-800">{t(networkError(read.error))}</p> : null}
    {transfer && !locked && <div className="space-y-4">
      <div><h3 className="break-words font-bold">{transfer.label}</h3>
        <TransferStatus transfer={expiredPending ? { ...transfer, status: 'expired' } : transfer} /></div>
      {view === 'received' && transfer.status === 'pending' && !expiredPending && <p className="text-sm">{t('custody.pending_help')}</p>}
      {transfer.fields && <section className="space-y-3 rounded-xl border border-line p-3">
        <h4 className="font-semibold">{t(view === 'sent' ? 'custody.offer_fields' : 'custody.transferred_fields')}</h4>
        <CustodyPropertyFields fields={transfer.fields} classes={classes} />
        {view === 'sent' && <p className="text-sm text-dim">{t('custody.snapshot_help')}</p>}
      </section>}
      {view === 'received' && transfer.status === 'accepted' && transfer.fields && transfer.property_id
        && <button type="button" className={NETWORK_PRIMARY} onClick={() => {
          if (transfer.property_id && allowNetworkNavigation()) onProperty(transfer.property_id)
        }}>{t('custody.open_property')}</button>}
      {!expiredPending && (confirmation && transfer.actions.includes(confirmation)
        ? <section className="space-y-3 rounded-xl border border-line p-3">
          <p className="text-sm">{t(`custody.confirmations.${confirmation}`)}</p>
          {confirmation === 'accept' && <>
            <p className="text-sm">{t('custody.exclusions')}</p>
            <label className="flex min-h-11 items-start gap-3 text-sm font-semibold">
              <input type="checkbox" checked={acceptReviewed} onChange={event => setAcceptReviewed(event.target.checked)} />
              {t('custody.accept_consent')}
            </label>
          </>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={NETWORK_PRIMARY} disabled={confirmation === 'accept' && !acceptReviewed}
              onClick={() => confirm(confirmation)}>{t(`custody.confirm.${confirmation}`)}</button>
            <button type="button" className={NETWORK_BUTTON} onClick={() => {
              setConfirmation(null); setAcceptReviewed(false)
            }}>{t('custody.keep_reviewing')}</button>
          </div>
        </section>
        : <div className="flex flex-wrap gap-2">
          {transfer.actions.map(action => <button key={action} type="button" className={NETWORK_BUTTON}
            onClick={() => setConfirmation(action)}>{t(`custody.actions.${action}`)}</button>)}
        </div>)}
    </div>}
    <ServiceMutationStatus mutation={{ ...mutation, saved: mutation.saved && transfer !== null }} />
  </NetworkPanel>
}
