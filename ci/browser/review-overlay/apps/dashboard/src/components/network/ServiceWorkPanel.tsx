import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getServiceWork, MAX_VISIT_MINUTES, MIN_VISIT_MINUTES, prepareServiceWorkAction, resolveServiceWorkAction,
  runServiceWorkAction, santoDomingoVisitStart, validVisitMinutes, validWorkDay, validWorkNote,
  VISIT_TIME_ZONE, WORK_NOTE_LIMIT, type ServiceWork, type WorkAction, type WorkIntent, type WorkOperation,
} from '../../api/serviceWork'
import { todayInDR } from '../../api/propertyManagement'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { allowNetworkNavigation, NetworkDate, NetworkFreshness, NetworkPanel, NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'
import { ServiceReviewPanel } from './ServiceReviewPanel'

type Draft = {
  operation: WorkOperation
  version: number
  startsAt: string
  duration: string
  note: string
  completedOn: string
  shareInHistory: boolean
}

const workTransport = {
  prepare: prepareServiceWorkAction,
  execute: runServiceWorkAction,
  resolve: resolveServiceWorkAction,
  canRetry: (action: WorkAction, current: ServiceWork) => current.version === action.payload.expected_version && current.actions.includes(action.payload.operation),
}

function intentFor(draft: Draft, work: ServiceWork): WorkIntent | null {
  const operation = draft.operation
  switch (operation) {
    case 'propose_visit': {
      const start = santoDomingoVisitStart(draft.startsAt)
      const duration = Number(draft.duration)
      return start && Date.parse(start) > Date.now() && validVisitMinutes(duration)
        ? { operation, starts_at: start, duration_minutes: duration } : null
    }
    case 'request_completion': {
      const confirmed = work.confirmed_visit
      if (!confirmed || Date.parse(confirmed.starts_at) > Date.now() || !validWorkNote(draft.note) || !validWorkDay(draft.completedOn)
        || draft.completedOn < todayInDR(new Date(confirmed.starts_at)) || draft.completedOn > todayInDR()) return null
      return { operation, note: draft.note.trim(), completed_on: draft.completedOn }
    }
    case 'request_changes':
      return validWorkNote(draft.note) ? { operation, note: draft.note.trim() } : null
    case 'approve_completion':
      return draft.shareInHistory ? { operation, share_in_history: true } : null
    case 'accept_visit':
      return work.proposed_visit && Date.parse(work.proposed_visit.starts_at) > Date.now() ? { operation } : null
    case 'decline_visit': case 'cancel_visit':
      return { operation }
  }
}

export function ServiceWorkPanel({ requestId, projectId, parentFresh, reviewSender, onProject }: {
  requestId: string; projectId: string; parentFresh: boolean; reviewSender: 'owner' | 'provider' | null; onProject: (id: string) => void
}) {
  const { t, i18n } = useTranslation('property_network')
  const read = useNetworkRead(`service-work:${requestId}:${projectId}`, signal => getServiceWork(requestId, projectId, signal))
  const [draft, setDraft] = useState<Draft | null>(null)
  const [latest, setLatest] = useState<ServiceWork | null>(null)
  const readError = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(workTransport, signal => getServiceWork(requestId, projectId, signal), value => {
    setLatest(value)
    setDraft(null)
    read.refresh()
  }, current => {
    setLatest(current)
    setDraft(previous => previous && { ...previous, version: current.version, shareInHistory: false })
    read.refresh()
  }, readError === 'error_access')
  const denied = readError === 'error_access' || mutation.denied
  const work = latest && (!read.data || latest.version > read.data.version) ? latest : read.data
  const fresh = parentFresh && !!work && read.data?.version === work.version && !read.checking && !read.stale && !read.error
  const locked = mutation.busy || mutation.pending !== null
  useNetworkDraftGuard(!denied && (draft !== null || mutation.pending !== null), !denied && mutation.busy)

  function begin(operation: WorkOperation) {
    if (!work || !fresh || locked || draft || !work.actions.includes(operation)) return
    setDraft({ operation, version: work.version, startsAt: '', duration: '60',
      note: operation === 'request_completion' ? work.completion_note ?? '' : '',
      completedOn: operation === 'request_completion' ? work.completed_on ?? todayInDR() : '', shareInHistory: false })
  }

  const allowed = !!(draft && work?.actions.includes(draft.operation))
  const versionCurrent = !!draft && draft.version === work?.version
  const intent = draft && work && intentFor(draft, work)
  const retryCurrent = !!(fresh && work && mutation.pending && workTransport.canRetry(mutation.pending, work))
  function submit() {
    if (!work || !draft || !fresh || locked || !allowed || !versionCurrent) return
    const currentIntent = intentFor(draft, work)
    if (!currentIntent) return
    mutation.send({ requestId, projectId, payload: { ...currentIntent, submission_id: crypto.randomUUID(), expected_version: draft.version } })
  }

  if (denied) return <NetworkPanel title={t('work.title')}><p role="alert">{t('error_access')}</p></NetworkPanel>

  const visitTime = (value: string) => new Intl.DateTimeFormat(i18n.language.startsWith('es') ? 'es-DO' : 'en-US', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: VISIT_TIME_ZONE,
  }).format(new Date(value))
  const firstCompletionDate = work?.confirmed_visit ? todayInDR(new Date(work.confirmed_visit.starts_at)) : undefined
  return <><NetworkPanel title={t('work.title')}>
    <NetworkFreshness read={read} />
    <p className="mb-4 text-sm text-dim">{t('work.privacy')}</p>
    {readError ? <p role="alert" className="text-sm text-red-800">{t(readError)}</p> : null}
    {work ? <>
      <p className="text-sm font-semibold">{t(`work.states.${work.status}`)}</p>
      <dl className="my-4 space-y-3 text-sm">
        {work.confirmed_visit ? <div><dt className="font-semibold">{t('work.confirmed_visit')}</dt><dd><time dateTime={work.confirmed_visit.starts_at}>{visitTime(work.confirmed_visit.starts_at)}</time> · {t('work.minutes', { count: work.confirmed_visit.duration_minutes })}</dd></div> : null}
        {work.proposed_visit ? <div><dt className="font-semibold">{t(`work.proposed_visit.${work.proposed_visit.proposed_by}`)}</dt><dd><time dateTime={work.proposed_visit.starts_at}>{visitTime(work.proposed_visit.starts_at)}</time> · {t('work.minutes', { count: work.proposed_visit.duration_minutes })}</dd></div> : null}
        {work.confirmed_visit || work.proposed_visit ? <div><dt className="sr-only">{t('work.time_zone_label')}</dt><dd className="text-xs text-dim">{t('work.time_zone')}</dd></div> : null}
        {work.completed_on ? <div><dt className="font-semibold">{t('work.completed_on')}</dt><dd><NetworkDate value={work.completed_on} /></dd></div> : null}
        {work.completion_note ? <div><dt className="font-semibold">{t('work.completion_summary')}</dt><dd className="break-words whitespace-pre-wrap">{work.completion_note}</dd></div> : null}
        {work.completion_review_note ? <div><dt className="font-semibold">{t('work.private_review_note')}</dt><dd className="break-words whitespace-pre-wrap">{work.completion_review_note}</dd></div> : null}
      </dl>
      {work.completion_update_id ? <button type="button" className={NETWORK_BUTTON} disabled={!fresh || locked || !!draft} onClick={() => { if (allowNetworkNavigation()) onProject(projectId) }}>{t('work.open_history')}</button> : null}
      <div className="my-4 flex flex-wrap gap-2">
        {work.actions.map(operation => <button type="button" className={NETWORK_BUTTON} disabled={!fresh || locked || !!draft} key={operation} onClick={() => begin(operation)}>{t(`work.actions.${operation}`)}</button>)}
      </div>
      {draft ? <form className="space-y-4 rounded-xl border border-line bg-white p-4" onSubmit={event => { event.preventDefault(); submit() }}>
        <h3 className="font-semibold">{t(`work.actions.${draft.operation}`)}</h3>
        <p className="text-sm text-dim">{t(`work.help.${draft.operation}`)}</p>
        <fieldset className="min-w-0 space-y-3" disabled={locked}>
          {draft.operation === 'propose_visit' ? <div className="grid gap-3 sm:grid-cols-2">
            <label className="block min-w-0 text-sm font-semibold">{t('work.visit_start')}<input className={NETWORK_FIELD} type="datetime-local" required step={60} value={draft.startsAt} onChange={event => setDraft({ ...draft, startsAt: event.target.value })} /></label>
            <label className="block min-w-0 text-sm font-semibold">{t('work.duration')}<input className={NETWORK_FIELD} type="number" required step={1} min={MIN_VISIT_MINUTES} max={MAX_VISIT_MINUTES} value={draft.duration} onChange={event => setDraft({ ...draft, duration: event.target.value })} /></label>
          </div> : null}
          {draft.operation === 'request_completion' ? <label className="block text-sm font-semibold">{t('work.completed_on')}<input className={NETWORK_FIELD} type="date" required min={firstCompletionDate} max={todayInDR()} value={draft.completedOn} onChange={event => setDraft({ ...draft, completedOn: event.target.value })} /></label> : null}
          {draft.operation === 'request_completion' || draft.operation === 'request_changes' ? <label className="block text-sm font-semibold">{t(draft.operation === 'request_changes' ? 'work.private_review_note' : 'work.completion_summary')}<textarea className={NETWORK_FIELD} required rows={4} maxLength={WORK_NOTE_LIMIT} value={draft.note} onChange={event => setDraft({ ...draft, note: event.target.value })} /></label> : null}
          {draft.operation === 'approve_completion' ? <label className="flex min-h-11 items-start gap-2 text-sm"><input className="mt-1" type="checkbox" required checked={draft.shareInHistory} onChange={event => setDraft({ ...draft, shareInHistory: event.target.checked })} />{t('work.share_in_history')}</label> : null}
          {!allowed ? <p role="status" className="text-sm text-amber-950">{t('work.action_unavailable')}</p> : !versionCurrent ? <div className="space-y-2"><p role="status" className="text-sm text-amber-950">{t('service.changed_review')}</p><button type="button" className={NETWORK_BUTTON} disabled={!fresh} onClick={() => setDraft({ ...draft, version: work.version, shareInHistory: false })}>{t('service.review_current')}</button></div> : null}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={NETWORK_PRIMARY} disabled={!fresh || locked || !allowed || !versionCurrent || !intent}>{t('work.confirm_action')}</button>
            <button type="button" className={NETWORK_BUTTON} onClick={() => { if (allowNetworkNavigation()) setDraft(null) }}>{t('service.close_draft')}</button>
          </div>
        </fieldset>
      </form> : null}
    </> : !readError ? <p role="status">{t('loading')}</p> : null}
    <ServiceMutationStatus mutation={{ ...mutation,
      checked: mutation.checked && (mutation.conflicted || retryCurrent),
      retry: () => { if (retryCurrent) mutation.retry() },
    }} />
  </NetworkPanel>
    {reviewSender && work?.status === 'completed' && work.completion_update_id ? <ServiceReviewPanel
      key={`${requestId}:${projectId}:${work.completion_update_id}:${reviewSender}`}
      requestId={requestId} sender={reviewSender} parentFresh={fresh && !locked} /> : null}
  </>
}
