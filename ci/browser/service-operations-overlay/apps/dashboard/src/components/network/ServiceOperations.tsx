import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { getServiceOperations, type ServiceOperationsItem, type ServiceOperationsScope, type ServiceOperationsView } from '../../api/serviceOperations'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, networkError } from './NetworkShared'

type Props = {
  actorId: string
  scope: ServiceOperationsScope
  onScope: (scope: ServiceOperationsScope) => void
  onOpen: (id: string, view: ServiceOperationsView) => void
}

function OperationDates({ item }: { item: ServiceOperationsItem }) {
  const { t } = useTranslation('property_network')
  if (!item.desired_start_on && !item.quote_valid_until && !item.starts_at) return null
  return <dl className="grid gap-2 text-sm sm:grid-cols-2">
    {item.desired_start_on && <div><dt className="font-semibold">{t('operations.desired_start')}</dt><dd><NetworkDate value={item.desired_start_on} /></dd></div>}
    {item.quote_valid_until && <div><dt className="font-semibold">{t('operations.quote_valid_until')}</dt><dd><NetworkDate value={item.quote_valid_until} /></dd></div>}
    {item.starts_at && <div><dt className="font-semibold">{t('operations.confirmed_visit')}</dt><dd><NetworkDate value={item.starts_at} showTime /></dd></div>}
  </dl>
}

export function ServiceOperations({ actorId, scope, onScope, onOpen }: Props) {
  const { t } = useTranslation('property_network')
  const headingId = useId()
  const key = `service-operations:${actorId}:${scope.view}:${scope.page}:${scope.source?.kind ?? ''}:${scope.source?.id ?? ''}`
  const read = useNetworkRead(key, signal => getServiceOperations(scope, signal))
  const error = read.error ? networkError(read.error) : null
  const data = error || read.stale ? null : read.data

  return <NetworkPanel title={t('operations.title')} headingId={headingId}>
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label={t('operations.views')}>
      {(['managed', 'received'] as const).map(view => <button type="button" key={view} aria-pressed={view === scope.view}
        className={view === scope.view ? NETWORK_PRIMARY : NETWORK_BUTTON} onClick={() => {
          if (view !== scope.view) onScope({ view, page: 1 })
        }}>{t(`operations.${view}`)}</button>)}
    </div>
    {scope.source && <div className="mb-4 flex flex-wrap items-center gap-2">
      <p className="text-sm">{t('operations.filtered')}</p>
      <button type="button" className={NETWORK_BUTTON} onClick={() => onScope({ view: 'managed', page: 1 })}>{t('operations.clear_filter')}</button>
    </div>}
    {error !== 'error_access' && <NetworkFreshness read={read} />}
    {error ? <p role="alert">{t(error)}</p> : !data ? <p role="status">{t('loading')}</p> : <>
      <p className="mb-4 flex flex-wrap gap-x-1 text-sm text-dim">
        <span>{t('operations.as_of')}</span> <NetworkDate value={data.as_of} /> <span>· {t('operations.time_zone')}</span>
      </p>
      {data.items.length ? <div className="grid gap-3 lg:grid-cols-2">
        {data.items.map(item => <article key={item.request_id} className="min-w-0 space-y-3 break-words rounded-xl border border-line bg-white p-4">
          <p className="text-sm font-semibold text-sea">{t(`operations.stages.${item.stage}`)}</p>
          <h3 className="font-bold">{item.title}</h3>
          {item.source && <p className="text-sm text-dim">{item.source.title}</p>}
          <OperationDates item={item} />
          {item.actions.length > 0 && <div>
            <p className="text-sm font-semibold">{t('operations.actions_in_request')}</p>
            <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-ink2">
              {item.actions.map(action => <li key={action}>{t(`operations.actions.${action}`)}</li>)}
            </ul>
          </div>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={NETWORK_PRIMARY} onClick={() => onOpen(item.request_id, data.view)}>{t('service.open_request')}</button>
            {item.source && !scope.source && <button type="button" className={NETWORK_BUTTON} onClick={() => {
              if (item.source) onScope({ view: 'managed', page: 1, source: { kind: item.source.kind, id: item.source.id } })
            }}>{t('operations.filter_property')}</button>}
          </div>
        </article>)}
      </div> : <p role="status">{t(`operations.empty_${scope.view}`)}</p>}
      <NetworkPagination page={scope.page} total={data.total} busy={read.checking} onChange={page => onScope({ ...scope, page })} />
    </>}
  </NetworkPanel>
}
