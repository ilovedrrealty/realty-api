import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getMaintenanceReminders, normalizeReminderTitle, prepareReminderIntent, reminderActions, resolveReminderIntent, runReminderIntent,
  REMINDER_LEAD_LIMIT, REMINDER_PAGE_SIZE, REMINDER_REPEATS, REMINDER_TITLE_LIMIT, type MaintenanceReminder, type MaintenanceReminderPage,
  type ReminderAction, type ReminderIntent, type ReminderPreference, type ReminderRepeat, type ReminderSource,
} from '../../api/maintenanceReminders'
import { validCalendarDate } from '../../api/propertyManagement'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import {
  NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard,
} from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'

type PreferenceDraft = { enabled: boolean; lead: string; version: number }
type ScheduleDraft = { title: string; dueOn: string; repeat: ReminderRepeat }
type SnoozeDraft = { id: string; version: number; until: string }
const emptySchedule: ScheduleDraft = { title: '', dueOn: '', repeat: 'none' }
const fromPreference = (value: ReminderPreference): PreferenceDraft => ({ enabled: value.enabled, lead: String(value.lead_days), version: value.version })

const transport = {
  prepare: prepareReminderIntent,
  execute: runReminderIntent,
  resolve: resolveReminderIntent,
  canRetry: (intent: ReminderIntent, current: MaintenanceReminderPage) => {
    if (intent.operation === 'preferences') return current.preference.version === intent.expected_version
    if (intent.operation === 'create') return true
    const item = current.items.find(row => row.id === intent.schedule_id)
    return !!item && item.version === intent.expected_version && reminderActions(item).includes(intent.operation)
      && (intent.operation !== 'snooze' || intent.snoozed_until > current.as_of)
  },
}

function nextDay(value: string): string {
  const current = new Date(`${value}T12:00:00Z`)
  current.setUTCDate(current.getUTCDate() + 1)
  return current.toISOString().slice(0, 10)
}

export function MaintenanceReminders({ source, parentFresh = true }: { source: ReminderSource; parentFresh?: boolean }) {
  const { t } = useTranslation('property_network')
  const fieldId = useId()
  const [page, setPage] = useState(1)
  const [preferences, setPreferences] = useState<PreferenceDraft | null>(null)
  const [confirmedPreferences, setConfirmedPreferences] = useState<ReminderPreference | null>(null)
  const [draft, setDraft] = useState<ScheduleDraft>(emptySchedule)
  const [snooze, setSnooze] = useState<SnoozeDraft | null>(null)
  const load = (signal: AbortSignal) => getMaintenanceReminders(source, page, signal)
  const read = useNetworkRead(`maintenance-reminders:${source.kind}:${source.id}:${page}`, load)
  const data = read.data
  const readError = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(transport, load, value => {
    if (value.operation === 'preferences') { setConfirmedPreferences(value.result); setPreferences(null) }
    if (value.operation === 'create') setDraft(emptySchedule)
    if (value.operation === 'snooze') setSnooze(null)
    if (value.operation === 'create' && page !== 1) setPage(1)
    else read.refresh()
  }, current => {
    setPreferences(previous => previous?.version === current.preference.version ? previous : null)
    setSnooze(previous => previous && current.items.some(item => item.id === previous.id && item.version === previous.version) ? previous : null)
    read.refresh()
  }, readError === 'error_access')
  const denied = readError === 'error_access' || mutation.denied
  const settings = data && confirmedPreferences && confirmedPreferences.version > data.preference.version ? confirmedPreferences : data?.preference
  const fresh = parentFresh && !!data && data.preference.version === settings?.version && !read.stale && !read.checking && !read.error && !denied
  const locked = mutation.busy || mutation.pending !== null
  const currentPrefs = preferences ?? (settings ? fromPreference(settings) : null)
  const preferencesCurrent = !!settings && currentPrefs?.version === settings.version
  const leadDays = Number(currentPrefs?.lead)
  const validLead = currentPrefs?.lead !== '' && Number.isInteger(leadDays) && leadDays >= 0 && leadDays <= REMINDER_LEAD_LIMIT
  const changedPrefs = !!settings && !!currentPrefs && (settings.enabled !== currentPrefs.enabled || String(settings.lead_days) !== currentPrefs.lead)
  const normalizedTitle = normalizeReminderTitle(draft.title)
  const validCreate = !!normalizedTitle && validCalendarDate(draft.dueOn)
  const snoozeOpen = !!snooze && !!data?.items.some(item => item.id === snooze.id)
  const dirty = changedPrefs || draft.title !== '' || draft.dueOn !== '' || draft.repeat !== 'none' || (snoozeOpen && !!snooze?.until)
  useNetworkDraftGuard(!denied && (dirty || mutation.pending !== null), !denied && mutation.busy)

  function savePreferences() {
    if (!fresh || locked || !currentPrefs || !preferencesCurrent || !validLead || !changedPrefs) return
    mutation.send({ source, operation: 'preferences', submission_id: crypto.randomUUID(), expected_version: currentPrefs.version,
      enabled: currentPrefs.enabled, lead_days: leadDays })
  }

  function create() {
    if (!fresh || locked || !validCreate || !normalizedTitle) return
    mutation.send({ source, operation: 'create', submission_id: crypto.randomUUID(), title: normalizedTitle,
      repeat: draft.repeat, first_due_on: draft.dueOn })
  }

  function scheduleAction(item: MaintenanceReminder, operation: ReminderAction) {
    if (!fresh || locked || snoozeOpen || !reminderActions(item).includes(operation)) return
    if (operation === 'snooze') { setSnooze({ id: item.id, version: item.version, until: '' }); return }
    mutation.send({ source, operation, submission_id: crypto.randomUUID(), schedule_id: item.id, expected_version: item.version })
  }

  function saveSnooze(item: MaintenanceReminder) {
    if (!fresh || locked || !data || !snooze || snooze.id !== item.id || snooze.version !== item.version
      || !reminderActions(item).includes('snooze') || !validCalendarDate(snooze.until) || snooze.until <= data.as_of) return
    mutation.send({ source, operation: 'snooze', submission_id: crypto.randomUUID(), schedule_id: item.id,
      expected_version: snooze.version, snoozed_until: snooze.until })
  }

  if (denied) return null

  return <NetworkPanel title={t('reminders.title')} headingId={`${fieldId}-heading`}>
    <NetworkFreshness read={read} />
    <p className="mb-3 text-sm text-dim">{t('reminders.in_app')}</p>
    {readError ? <p role="alert" className="text-sm text-red-800">{t(readError)}</p> : null}
    {!data && !readError ? <p role="status">{t('loading')}</p> : null}
    {data && currentPrefs ? <div className="space-y-5">
      <p className="text-xs text-dim">{t('reminders.today')} <NetworkDate value={data.as_of} /></p>
      <form onSubmit={event => { event.preventDefault(); savePreferences() }} className="rounded-xl border border-line bg-white p-4">
        <fieldset disabled={!fresh || locked} className="space-y-3">
          <legend className="font-semibold text-ink">{t('reminders.preferences')}</legend>
          <div className="flex min-h-11 items-center gap-3">
            <input id={`${fieldId}-enabled`} type="checkbox" checked={currentPrefs.enabled} onChange={event=>setPreferences({ ...currentPrefs, enabled: event.target.checked })} />
            <label htmlFor={`${fieldId}-enabled`} className="text-sm font-semibold">{t('reminders.enable')}</label>
          </div>
          <div className="max-w-xs">
            <label htmlFor={`${fieldId}-lead`} className="block text-sm font-semibold">{t('reminders.lead_days')}</label>
            <input id={`${fieldId}-lead`} className={NETWORK_FIELD} type="number" min={0} max={REMINDER_LEAD_LIMIT} step={1}
              required value={currentPrefs.lead} onChange={event=>setPreferences({ ...currentPrefs, lead: event.target.value })} />
          </div>
          {!preferencesCurrent ? <div className="space-y-2"><p role="status" className="text-sm text-amber-950">{t('reminders.changed')}</p>
            <button type="button" className={NETWORK_BUTTON} onClick={()=>setPreferences(null)}>{t('reminders.use_current')}</button></div> : null}
          <button type="submit" className={NETWORK_PRIMARY} disabled={!preferencesCurrent || !changedPrefs || !validLead}>{t('reminders.save_preferences')}</button>
        </fieldset>
      </form>

      <form onSubmit={event=>{event.preventDefault();create()}} className="rounded-xl border border-line bg-white p-4">
        <fieldset disabled={!fresh || locked} className="space-y-3">
          <legend className="font-semibold text-ink">{t('reminders.new')}</legend>
          <div><label htmlFor={`${fieldId}-title`} className="block text-sm font-semibold">{t('reminders.field_title')}</label>
            <input id={`${fieldId}-title`} className={NETWORK_FIELD} value={draft.title} maxLength={REMINDER_TITLE_LIMIT * 2} required
              onChange={event=>setDraft({...draft,title:event.target.value})} /></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="min-w-0"><label htmlFor={`${fieldId}-first`} className="block text-sm font-semibold">{t('reminders.first_due')}</label>
              <input id={`${fieldId}-first`} className={NETWORK_FIELD} type="date" required value={draft.dueOn} onChange={event=>setDraft({...draft,dueOn:event.target.value})} /></div>
            <div><label htmlFor={`${fieldId}-repeat`} className="block text-sm font-semibold">{t('reminders.repeat')}</label>
              <select id={`${fieldId}-repeat`} className={NETWORK_FIELD} value={draft.repeat} onChange={event=>setDraft({...draft,repeat:event.target.value as ReminderRepeat})}>
                {REMINDER_REPEATS.map(value=><option key={value} value={value}>{t(`reminders.repeats.${value}`)}</option>)}
              </select></div>
          </div>
          <button type="submit" className={NETWORK_PRIMARY} disabled={!validCreate}>{t('reminders.add')}</button>
        </fieldset>
      </form>

      {data.items.length ? <ul className="space-y-3">
        {data.items.map(item => {
          const activeSnooze = snooze?.id === item.id ? snooze : null
          return <li key={item.id} className="min-w-0 space-y-3 rounded-xl border border-line bg-white p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="break-words font-semibold text-ink">{item.title}</h3>
              <p className="font-semibold text-ink2">{t(`reminders.status.${item.status}`)}{item.attention !== 'none' ? ` · ${t(`reminders.attention.${item.attention}`)}` : ''}</p></div>
            <dl className="flex flex-wrap gap-x-5 gap-y-2">
              <div><dt className="font-semibold">{t('reminders.due')}</dt><dd><NetworkDate value={item.due_on} /></dd></div>
              <div><dt className="font-semibold">{t('reminders.repeat')}</dt><dd>{t(`reminders.repeats.${item.repeat}`)}</dd></div>
              {item.snoozed_until ? <div><dt className="font-semibold">{t('reminders.snoozed_until')}</dt><dd><NetworkDate value={item.snoozed_until} /></dd></div> : null}
            </dl>
            {reminderActions(item).length ? <div className="flex flex-wrap gap-2">{reminderActions(item).map(action=><button type="button" key={action}
              className={NETWORK_BUTTON} disabled={!fresh || locked || snoozeOpen} onClick={()=>scheduleAction(item,action)}>{t(`reminders.actions.${action}`)}</button>)}</div> : null}
            {activeSnooze ? <form className="space-y-2" onSubmit={event=>{event.preventDefault();saveSnooze(item)}}>
              <label htmlFor={`${fieldId}-snooze`} className="block text-sm font-semibold">{t('reminders.snooze_until')}</label>
              <input id={`${fieldId}-snooze`} className={`${NETWORK_FIELD} max-w-xs`} type="date" min={nextDay(data.as_of)} required value={activeSnooze.until}
                disabled={!fresh || locked || activeSnooze.version !== item.version} onChange={event=>setSnooze({...activeSnooze,until:event.target.value})} />
              {activeSnooze.version !== item.version ? <p role="status" className="text-sm text-amber-950">{t('reminders.changed')}</p> : null}
              <div className="flex flex-wrap gap-2">
                <button type="submit" className={NETWORK_PRIMARY} disabled={!fresh || locked || activeSnooze.version !== item.version || !validCalendarDate(activeSnooze.until) || activeSnooze.until <= data.as_of}>{t('reminders.save_snooze')}</button>
                <button type="button" className={NETWORK_BUTTON} disabled={locked} onClick={()=>setSnooze(null)}>{t('reminders.cancel_snooze')}</button>
              </div>
            </form> : null}
          </li>
        })}
      </ul> : <p className="text-sm text-dim">{t('reminders.empty')}</p>}
      {data.total > REMINDER_PAGE_SIZE || page > 1 ? <NetworkPagination page={page} total={data.total}
        busy={read.checking || locked} onChange={value=>{setSnooze(null);setPage(value)}} /> : null}
    </div> : null}
    <ServiceMutationStatus mutation={{ ...mutation, checked: mutation.checked && fresh,
      retry: () => { if (fresh) mutation.retry() }, reviewConflict: () => { if (fresh) mutation.reviewConflict() } }} />
  </NetworkPanel>
}
