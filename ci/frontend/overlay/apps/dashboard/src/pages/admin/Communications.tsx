import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { UserInfo } from '../../lib/auth'
import type { Role } from '../../App'
import { COMMUNICATION_STATUSES, CommunicationRetryError, authorizeCommunicationRetry, getCommunications,
  type CommunicationEvent, type CommunicationJob, type CommunicationStatus, type CommunicationsReport } from '../../api/communications'

const PAGE_SIZE = 25
const REPORT_MAX_AGE_MS = 180_000
const button = 'min-h-11 rounded-lg border border-line px-3 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-40'
type Notice = 'authorized_active' | 'authorized_paused' | 'not_retryable' | 'unconfirmed' | 'stale'

export function Communications({ user, workspace }: { user: Pick<UserInfo, 'role'>; workspace: Role }) {
  const { t } = useTranslation('communications')
  // A URL/workspace choice cannot turn a non-Admin account into an Admin.
  // Keep effects in the authorized child so denied views make no API request.
  return user.role === 'admin' && workspace === 'Admin'
    ? <CommunicationsContent /> : <p role="alert" className="text-sm text-ink2">{t('denied')}</p>
}

function CommunicationsContent() {
  const { t, i18n } = useTranslation('communications')
  const [status, setStatus] = useState<CommunicationStatus | ''>('')
  const [eventKind, setEventKind] = useState<CommunicationEvent | ''>('')
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [report, setReport] = useState<CommunicationsReport | null>(null)
  const [loadedAt, setLoadedAt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [confirmation, setConfirmation] = useState<CommunicationJob | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const busyRef = useRef(false)
  const mounted = useRef(true)
  const confirmationRef = useRef<HTMLElement>(null)
  const locale = i18n.language.startsWith('es') ? 'es-DO' : 'en-US'
  const date = (value: string | null) => value ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : t('not_recorded')
  const stepLabel = (step: string) => /^email_\d+$/.test(step) ? t('steps.owner_email', { number: step.slice(6) }) : t(`steps.${step}`)

  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true); setFailed(false); setReport(null); setLoadedAt(0); setConfirmation(null)
    getCommunications({ limit: PAGE_SIZE, offset, ...(status ? { status } : {}), ...(eventKind ? { event_kind: eventKind } : {}) }, controller.signal)
      .then(next => { if (active && !controller.signal.aborted) { setReport(next); setLoadedAt(Date.now()) } })
      .catch(() => { if (active && !controller.signal.aborted) setFailed(true) })
      .finally(() => { if (active && !controller.signal.aborted) setLoading(false) })
    return () => { active = false; controller.abort() }
  }, [status, eventKind, offset, refresh])
  useEffect(() => {
    if (confirmation) { confirmationRef.current?.scrollIntoView({ block: 'center' }); confirmationRef.current?.focus({ preventScroll: true }) }
  }, [confirmation])

  function currentEligible(job: CommunicationJob): boolean {
    return Boolean(report?.items.some(item => item.id === job.id && item.can_retry && !item.outcome_uncertain)
      && loadedAt > 0 && Date.now() - loadedAt <= REPORT_MAX_AGE_MS)
  }
  function requestRetry(job: CommunicationJob) {
    if (busyRef.current) return
    if (!currentEligible(job)) { setNotice('stale'); setConfirmation(null); setRefresh(value => value + 1); return }
    setNotice(null); setConfirmation(job)
  }
  async function confirmRetry() {
    if (!confirmation || busyRef.current) return
    if (!currentEligible(confirmation)) { setNotice('stale'); setConfirmation(null); setRefresh(value => value + 1); return }
    busyRef.current = true; setRetrying(true); setNotice(null)
    try {
      const result = await authorizeCommunicationRetry(confirmation.id)
      if (mounted.current) setNotice(result.enabled ? 'authorized_active' : 'authorized_paused')
    } catch (error) {
      if (mounted.current) setNotice(error instanceof CommunicationRetryError && error.code === 'not_retryable' ? 'not_retryable' : 'unconfirmed')
    } finally {
      busyRef.current = false
      if (mounted.current) { setRetrying(false); setConfirmation(null); setRefresh(value => value + 1) }
    }
  }
  const locked = retrying || Boolean(confirmation)

  return <div className="space-y-5" aria-busy={loading}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-ink2"><p>{t('intro')}</p><p>{t('accepted_note')}</p><p className="text-xs">{t('history_note')}</p></div>
      <button type="button" className={button} disabled={loading || retrying} onClick={() => { setConfirmation(null); setNotice(null); setRefresh(value => value + 1) }}>{t('refresh')}</button>
    </div>
    {notice ? <p role={notice.startsWith('authorized_') ? 'status' : 'alert'} className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-relaxed text-amber-950">{t(`notices.${notice}`)}</p> : null}
    <section className="rounded-2xl border border-line bg-paper p-4 sm:p-5" aria-label={t('filters.title')}>
      <div className="flex flex-wrap gap-4">
        <label className="grid gap-2 text-sm font-semibold text-ink">{t('filters.event')}<select className="min-h-11 rounded-lg border border-line bg-paper px-3" value={eventKind} disabled={locked} onChange={e => { setEventKind(e.target.value as CommunicationEvent | ''); setOffset(0); setNotice(null) }}><option value="">{t('filters.all')}</option>{(['lead', 'signup'] as const).map(value => <option key={value} value={value}>{t(`events.${value}`)}</option>)}</select></label>
        <label className="grid gap-2 text-sm font-semibold text-ink">{t('filters.status')}<select className="min-h-11 rounded-lg border border-line bg-paper px-3" value={status} disabled={locked} onChange={e => { setStatus(e.target.value as CommunicationStatus | ''); setOffset(0); setNotice(null) }}><option value="">{t('filters.all')}</option>{COMMUNICATION_STATUSES.map(value => <option key={value} value={value}>{t(`statuses.${value}`)}</option>)}</select></label>
      </div>
    </section>
    {loading ? <p role="status" className="rounded-xl border border-line p-5 text-sm text-ink2">{t('loading')}</p> : null}
    {failed ? <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-950"><p>{t('load_error')}</p><button type="button" className={`${button} mt-3`} onClick={() => setRefresh(value => value + 1)}>{t('refresh')}</button></div> : null}
    {report ? <>
      <section className="grid gap-3 rounded-2xl border border-line bg-paper p-4 text-sm sm:grid-cols-2 sm:p-5" aria-label={t('flags.title')}>
        <div><h2 className="font-semibold text-ink">{t('flags.worker')}</h2><p className="mt-1 text-ink2">{t(report.enabled ? 'flags.enabled' : 'flags.paused')}</p></div>
        <div><h2 className="font-semibold text-ink">{t('flags.customer')}</h2><p className="mt-1 text-ink2">{t(report.customer_messages_enabled && report.enabled ? 'flags.enabled' : 'flags.paused')}</p></div>
        <p className="text-xs leading-relaxed text-dim sm:col-span-2">{t('flags.note')}</p>
      </section>
      <section className="rounded-2xl border border-line bg-paper p-4 sm:p-5" aria-label={t('records')}>
        <p className="text-xs text-dim">{t('updated', { date: date(new Date(loadedAt).toISOString()) })}</p>
        {report.items.length ? <ul className="mt-4 grid gap-3 lg:grid-cols-2">{report.items.map(job => <li key={job.id} className="rounded-xl border border-line p-4 text-sm">
          <div className="flex flex-wrap items-start justify-between gap-2"><h3 className="font-semibold text-ink">{t(`events.${job.event_kind}`)} · {stepLabel(job.step)}</h3><span className="rounded-full bg-line-soft px-2.5 py-1 text-xs font-semibold text-ink">{t(`statuses.${job.status}`)}</span></div>
          <p className="mt-2 break-all font-mono text-xs text-dim">{t('reference', { id: job.id })}</p>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs text-ink2"><dt>{t('attempts')}</dt><dd>{job.attempts}/{job.max_attempts}</dd><dt>{t('created')}</dt><dd>{date(job.created_at)}</dd><dt>{t('last_attempt')}</dt><dd>{date(job.last_attempt_at)}</dd>{job.accepted_at ? <><dt>{t('accepted_at')}</dt><dd>{date(job.accepted_at)}</dd></> : null}</dl>
          {job.outcome_uncertain ? <p className="mt-3 text-xs font-semibold leading-relaxed text-amber-900">{t('uncertain')}</p> : job.error_code ? <p className="mt-3 text-xs leading-relaxed text-ink2">{t('error_code', { code: job.error_code })}</p> : null}
          {job.status === 'retry_wait' || job.status === 'failed' ? <p className="mt-2 text-xs leading-relaxed text-ink2">{t('eligible_after', { date: date(job.next_attempt_at) })}</p> : null}
          {job.can_retry ? <button type="button" className={`${button} mt-3`} disabled={locked} onClick={() => requestRetry(job)}>{t('retry')}</button> : <p className="mt-3 text-xs text-dim">{t('no_retry')}</p>}
        </li>)}</ul> : <p className="mt-4 rounded-xl bg-line-soft p-5 text-sm leading-relaxed text-ink2">{t('empty')}</p>}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-ink2"><p>{t('total', { count: report.total })}</p><div className="flex gap-2"><button type="button" className={button} disabled={locked || offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>{t('previous')}</button><button type="button" className={button} disabled={locked || offset + PAGE_SIZE >= report.total || offset + PAGE_SIZE > 10000} onClick={() => setOffset(value => value + PAGE_SIZE)}>{t('next')}</button></div></div>
      </section>
    </> : null}
    {confirmation ? <section ref={confirmationRef} tabIndex={-1} role="region" aria-label={t('confirm.title')} className="rounded-2xl border border-amber-300 bg-amber-50 p-5 text-amber-950 focus-visible:outline-2 focus-visible:outline-navy"><h2 className="font-semibold">{t('confirm.title')}</h2><p className="mt-2 break-all font-mono text-xs">{confirmation.id}</p><p className="mt-3 text-sm leading-relaxed">{t('confirm.body')}</p><div className="mt-4 flex flex-wrap gap-2"><button type="button" className={`${button} bg-navy text-white`} disabled={retrying} onClick={confirmRetry}>{t(retrying ? 'confirm.waiting' : 'confirm.authorize')}</button><button type="button" className={button} disabled={retrying} onClick={() => setConfirmation(null)}>{t('cancel')}</button></div></section> : null}
  </div>
}
