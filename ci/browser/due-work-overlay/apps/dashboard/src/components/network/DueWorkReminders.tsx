import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  DUE_WORK_LEAD_LIMIT, DUE_WORK_PAGE_SIZE, dueWorkActions, getDueWorkReminders, prepareDueWorkIntent,
  resolveDueWorkIntent, runDueWorkIntent, sameDueWorkOccurrence, type DueWorkAction, type DueWorkIntent,
  type DueWorkItem, type DueWorkPage, type DueWorkPreference,
} from '../../api/dueWorkReminders'
import { validCalendarDate } from '../../api/propertyManagement'
import { VISIT_TIME_ZONE } from '../../api/serviceWork'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import {
  NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel,
  networkError, useNetworkDraftGuard,
} from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

type PreferenceDraft = Omit<DueWorkPreference, 'lead_days'> & { lead: string }
type SnoozeDraft = Pick<DueWorkItem, 'kind' | 'id' | 'occurrence_version' | 'version'> & { until: string }

const fromPreference = (value: DueWorkPreference): PreferenceDraft => ({
  milestones_enabled: value.milestones_enabled, visits_enabled: value.visits_enabled, lead: String(value.lead_days), version: value.version,
})

const transport = {
  prepare: prepareDueWorkIntent,
  execute: runDueWorkIntent,
  resolve: resolveDueWorkIntent,
  canRetry: (intent: DueWorkIntent, current: DueWorkPage) => {
    if (intent.operation === 'preferences') return current.preference.version === intent.expected_version
    const item = current.items.find(row => sameDueWorkOccurrence(row, intent))
    return !!item && item.version === intent.expected_version && dueWorkActions(item).includes(intent.operation)
      && (intent.operation !== 'snooze' || intent.snoozed_until > current.as_of)
  },
}

export function DueWorkReminders({ projectId, parentFresh }: { projectId: string; parentFresh: boolean }) {
  const { t, i18n } = useTranslation('property_network')
  const fieldId = useId()
  const [page, setPage] = useState(1)
  const [draft, setDraft] = useState<PreferenceDraft | null>(null)
  const [confirmed, setConfirmed] = useState<{ settings: DueWorkPreference; previous: DueWorkPreference | null } | null>(null)
  const [snooze, setSnooze] = useState<SnoozeDraft | null>(null)
  const load = (signal: AbortSignal) => getDueWorkReminders(projectId, page, signal)
  const read = useNetworkRead(`due-work-reminders:${projectId}:${page}`, load)
  const data = read.data
  const readError = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(transport, load, outcome => {
    if (outcome.operation === 'preferences') { setConfirmed({ settings: outcome.result, previous: data?.preference ?? null }); setDraft(null) }
    if (outcome.operation === 'snooze') setSnooze(null)
    if (outcome.operation === 'preferences' && page !== 1) setPage(1)
    else read.refresh()
  }, current => {
    setDraft(previous => previous?.version === current.preference.version ? previous : null)
    setSnooze(previous => previous && current.items.some(item => sameDueWorkOccurrence(item, previous) && item.version === previous.version) ? previous : null)
    read.refresh()
  }, readError === 'error_access')
  const denied = readError === 'error_access' || mutation.denied
  const settings = data && confirmed && confirmed.previous === data.preference && confirmed.settings.version > data.preference.version
    ? confirmed.settings : data?.preference
  const fresh = parentFresh && !!data && data.preference.version === settings?.version && !read.stale && !read.checking && !read.error && !denied
  const locked = mutation.busy || mutation.pending !== null
  const current = draft ?? (settings ? fromPreference(settings) : null)
  const versionCurrent = !!settings && current?.version === settings.version
  const days = Number(current?.lead)
  const validDays = current?.lead !== '' && Number.isInteger(days) && days >= 0 && days <= DUE_WORK_LEAD_LIMIT
  const changed = !!settings && !!current && (settings.milestones_enabled !== current.milestones_enabled
    || settings.visits_enabled !== current.visits_enabled || String(settings.lead_days) !== current.lead)
  const activeSnooze = snooze && data?.items.some(item => sameDueWorkOccurrence(item, snooze)) ? snooze : null
  const retryCurrent = !!(fresh && data && mutation.pending && transport.canRetry(mutation.pending, data))
  useNetworkDraftGuard(!denied && (changed || !!activeSnooze?.until || mutation.pending !== null), !denied && mutation.busy)

  function savePreferences() {
    if (!fresh || locked || !current || !versionCurrent || !validDays || !changed) return
    mutation.send({ projectId, operation: 'preferences', submission_id: crypto.randomUUID(), expected_version: current.version,
      milestones_enabled: current.milestones_enabled, visits_enabled: current.visits_enabled, lead_days: days })
  }

  function act(item: DueWorkItem, operation: DueWorkAction) {
    if (!fresh || locked || activeSnooze || !dueWorkActions(item).includes(operation)) return
    if (operation === 'snooze') {
      setSnooze({ kind: item.kind, id: item.id, occurrence_version: item.occurrence_version, version: item.version, until: item.snoozed_until ?? '' })
      return
    }
    mutation.send({ projectId, operation, submission_id: crypto.randomUUID(), kind: item.kind, id: item.id,
      occurrence_version: item.occurrence_version, expected_version: item.version })
  }

  function saveSnooze(item: DueWorkItem) {
    if (!fresh || locked || !data || !activeSnooze || !sameDueWorkOccurrence(activeSnooze, item) || activeSnooze.version !== item.version
      || !dueWorkActions(item).includes('snooze') || !validCalendarDate(activeSnooze.until) || activeSnooze.until <= data.as_of) return
    mutation.send({ projectId, operation: 'snooze', submission_id: crypto.randomUUID(), kind: item.kind, id: item.id,
      occurrence_version: item.occurrence_version, expected_version: item.version, snoozed_until: activeSnooze.until })
  }

  if (denied) return null

  const visitTime = new Intl.DateTimeFormat(i18n.language.startsWith('es') ? 'es-DO' : 'en-US', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: VISIT_TIME_ZONE,
  })
  const items = data?.items.filter(item => item.kind === 'milestone' ? settings?.milestones_enabled : settings?.visits_enabled) ?? []

  return <NetworkPanel title={t('due_work.title')} headingId={`${fieldId}-heading`}>
    <NetworkFreshness read={read} />
    <p className="mb-3 text-sm text-dim">{t('due_work.in_app')}</p>
    {readError ? <p role="alert" className="text-sm text-red-800">{t(readError)}</p> : null}
    {!data && !readError ? <p role="status">{t('loading')}</p> : null}
    {data && current ? <div className="space-y-5">
      <p className="text-xs text-dim">{t('reminders.today')} <NetworkDate value={data.as_of} /></p>
      <form onSubmit={event=>{event.preventDefault();savePreferences()}} className="rounded-xl border border-line bg-white p-4">
        <fieldset disabled={!fresh || locked} className="space-y-3">
          <legend className="font-semibold text-ink">{t('reminders.preferences')}</legend>
          <div className="flex min-h-11 items-center gap-3">
            <input id={`${fieldId}-milestones`} type="checkbox" checked={current.milestones_enabled} onChange={event=>setDraft({...current,milestones_enabled:event.target.checked})} />
            <label htmlFor={`${fieldId}-milestones`} className="text-sm font-semibold">{t('due_work.milestones')}</label>
          </div>
          <div className="flex min-h-11 items-center gap-3">
            <input id={`${fieldId}-visits`} type="checkbox" checked={current.visits_enabled} onChange={event=>setDraft({...current,visits_enabled:event.target.checked})} />
            <label htmlFor={`${fieldId}-visits`} className="text-sm font-semibold">{t('due_work.visits')}</label>
          </div>
          <div className="max-w-xs"><label htmlFor={`${fieldId}-lead`} className="block text-sm font-semibold">{t('reminders.lead_days')}</label>
            <input id={`${fieldId}-lead`} className={NETWORK_FIELD} type="number" min={0} max={DUE_WORK_LEAD_LIMIT} step={1} required
              value={current.lead} onChange={event=>setDraft({...current,lead:event.target.value})} /></div>
          {!versionCurrent ? <div className="space-y-2"><p role="status" className="text-sm text-amber-950">{t('reminders.changed')}</p>
            <button type="button" className={NETWORK_BUTTON} onClick={()=>setDraft(null)}>{t('reminders.use_current')}</button></div> : null}
          <button type="submit" className={NETWORK_PRIMARY} disabled={!versionCurrent || !changed || !validDays}>{t('reminders.save_preferences')}</button>
        </fieldset>
      </form>
      {items.length ? <ul className="space-y-3">
        {items.map(item => {
          const editing = activeSnooze && sameDueWorkOccurrence(item, activeSnooze) ? activeSnooze : null
          const actions = dueWorkActions(item)
          return <li key={`${item.kind}:${item.id}:${item.occurrence_version}`} className="min-w-0 space-y-3 rounded-xl border border-line bg-white p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="min-w-0"><p className="text-xs text-dim">{t(`due_work.kind.${item.kind}`)}</p><h3 className="break-words font-semibold text-ink">{item.title}</h3></div>
              <p className="font-semibold text-ink2">{item.state === 'active' ? t(`reminders.attention.${item.attention}`) : t(`due_work.state.${item.state}`)}</p>
            </div>
            <dl className="flex flex-wrap gap-x-5 gap-y-2">
              <div><dt className="font-semibold">{t(item.starts_at ? 'due_work.visit_time' : 'reminders.due')}</dt>
                <dd>{item.starts_at && item.duration_minutes ? <><time dateTime={item.starts_at}>{visitTime.format(new Date(item.starts_at))}</time> · {t('work.minutes',{count:item.duration_minutes})}</>
                  : <NetworkDate value={item.due_on} />}</dd></div>
              {item.snoozed_until ? <div><dt className="font-semibold">{t('reminders.snoozed_until')}</dt><dd><NetworkDate value={item.snoozed_until} /></dd></div> : null}
            </dl>
            {actions.length ? <div className="flex flex-wrap gap-2">{actions.map(action=><button type="button" className={NETWORK_BUTTON} key={action}
              disabled={!fresh || locked || !!activeSnooze} onClick={()=>act(item,action)}>{t(`due_work.actions.${action}`)}</button>)}</div> : null}
            {editing ? <form className="space-y-2" onSubmit={event=>{event.preventDefault();saveSnooze(item)}}>
              <label htmlFor={`${fieldId}-snooze`} className="block text-sm font-semibold">{t('reminders.snooze_until')}</label>
              <input id={`${fieldId}-snooze`} className={`${NETWORK_FIELD} max-w-xs`} type="date" min={data.as_of} required value={editing.until}
                disabled={!fresh || locked || editing.version !== item.version} onChange={event=>setSnooze({...editing,until:event.target.value})} />
              {editing.version !== item.version ? <p role="status" className="text-sm text-amber-950">{t('reminders.changed')}</p> : null}
              <div className="flex flex-wrap gap-2">
                <button type="submit" className={NETWORK_PRIMARY} disabled={!fresh || locked || editing.version !== item.version || !validCalendarDate(editing.until) || editing.until <= data.as_of}>{t('reminders.save_snooze')}</button>
                <button type="button" className={NETWORK_BUTTON} disabled={locked} onClick={()=>setSnooze(null)}>{t('reminders.cancel_snooze')}</button>
              </div>
            </form> : null}
          </li>
        })}
      </ul> : <p className="text-sm text-dim">{t(settings?.milestones_enabled || settings?.visits_enabled ? 'due_work.empty' : 'due_work.disabled')}</p>}
      {data.total > DUE_WORK_PAGE_SIZE || page > 1 ? <NetworkPagination page={page} total={data.total}
        busy={read.checking || locked} onChange={value=>{setSnooze(null);setPage(value)}} /> : null}
    </div> : null}
    <ServiceMutationStatus mutation={{ ...mutation, checked: mutation.checked && fresh && (mutation.conflicted || retryCurrent),
      retry: () => { if (retryCurrent) mutation.retry() }, reviewConflict: () => { if (fresh) mutation.reviewConflict() } }} />
  </NetworkPanel>
}
