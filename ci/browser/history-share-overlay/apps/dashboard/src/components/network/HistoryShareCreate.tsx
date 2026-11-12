import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getHistoryShareOptions, HISTORY_SHARE_EXPIRIES, HISTORY_SHARE_MAX_ITEMS, HISTORY_SHARE_MAX_LABEL, HISTORY_SHARE_MAX_SUMMARY,
  prepareHistoryShare, resolveHistoryShare, runHistoryShare, validHistoryShareText,
  type HistoryShareCreate, type HistoryShareOption, type HistoryShareOptions, type HistoryShareSource,
} from '../../api/historyShares'
import { getProjects } from '../../api/propertyNetwork'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import type { ProfessionalOptions } from '../../../../../packages/professionals/src'
import { allowNetworkNavigation, NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness,
  NetworkPagination, NetworkPanel, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceMutationStatus, useServiceAttempt } from './ServiceWorkflowShared'
import { HistoryWorkMetadata } from './HistorySharing'

type Props = { source: HistoryShareSource; categories: ProfessionalOptions['categories']; parentFresh: boolean; onSent: () => void }
type Selected = HistoryShareOption & { summary: string }
type Draft = { recipient: string; label: string; expiry: '' | typeof HISTORY_SHARE_EXPIRIES[number]; selected: Selected[]; reviewed: boolean }
const empty: Draft = { recipient: '', label: '', expiry: '', selected: [], reviewed: false }

const transport = {
  prepare: prepareHistoryShare,
  execute: runHistoryShare,
  resolve: resolveHistoryShare,
  canRetry: (intent: HistoryShareCreate, options: HistoryShareOptions) =>
    options.anchor_project_id === intent.input.anchor_project_id && options.source_id === intent.input.source_id
    && options.source_kind === intent.input.source_kind && options.professionals.some(row => row.user_code === intent.input.recipient_user_code),
}

export function HistoryShareCreateControl(props: Props) {
  const { t } = useTranslation('property_network')
  const [opened, setOpened] = useState(false)
  return <div className="space-y-3">
    <button type="button" className={NETWORK_BUTTON} disabled={!props.parentFresh} aria-expanded={opened}
      onClick={() => { if (!opened || allowNetworkNavigation()) setOpened(value => !value) }}>
      {t(opened ? 'history_shares.close_draft' : 'history_shares.create')}
    </button>
    {opened && <HistoryShareDraft key={`${props.source.kind}:${props.source.id}`} {...props} />}
  </div>
}

function HistoryShareDraft({ source, categories, parentFresh, onSent }: Props) {
  const { t } = useTranslation('property_network')
  const heading = useId()
  const [anchorPage, setAnchorPage] = useState(1), [anchorId, setAnchorId] = useState(''), [page, setPage] = useState(1)
  const [draft, setDraft] = useState<Draft>(empty)
  const projects = useNetworkRead(`history-share-anchors:${source.kind}:${source.id}:${anchorPage}`, signal => getProjects({
    page: anchorPage, ...(source.kind === 'private' ? { property_id: source.id } : { listing_id: source.id }),
  }, signal), parentFresh && !anchorId)
  const load = (signal: AbortSignal) => getHistoryShareOptions(source, anchorId, page, signal)
  const options = useNetworkRead(`history-share-options:${source.kind}:${source.id}:${anchorId}:${page}`, load, parentFresh && !!anchorId)
  const read = anchorId ? options : projects
  const error = read.error ? networkError(read.error) : ''
  const mutation = useServiceAttempt(transport, load, () => setDraft(empty), () => options.refresh(), error === 'error_access')
  const denied = error === 'error_access' || mutation.denied
  const visible = parentFresh && !read.stale && !error && !denied
  const fresh = visible && !read.checking
  const locked = mutation.busy || mutation.pending !== null
  const dirty = !!(draft.recipient || draft.label || draft.expiry || draft.selected.length || mutation.pending)
  useNetworkDraftGuard(dirty && !denied && !mutation.saved, mutation.busy)
  useEffect(() => { if (denied) setDraft(empty) }, [denied])

  const data = visible ? options.data : null
  const recipient = data?.professionals.find(item => String(item.user_code) === draft.recipient)
  const ready = !!recipient && draft.expiry !== '' && draft.reviewed && validHistoryShareText(draft.label, HISTORY_SHARE_MAX_LABEL)
    && draft.selected.length > 0 && draft.selected.every(item => validHistoryShareText(item.summary, HISTORY_SHARE_MAX_SUMMARY))
  const anchors = visible ? projects.data?.items.filter(project => project.my_role === 'owner'
    && (project.status === 'open' || project.status === 'in_progress')) ?? [] : []

  function edit(change: Partial<Draft>) { setDraft(previous => ({ ...previous, ...change, reviewed: false })) }
  function toggle(item: HistoryShareOption, selected: boolean) {
    setDraft(previous => ({ ...previous, reviewed: false, selected: selected
      ? [...previous.selected, { ...item, summary: '' }] : previous.selected.filter(row => row.update_id !== item.update_id) }))
  }
  function updateSummary(identity: string, summary: string) {
    setDraft(previous => ({ ...previous, reviewed: false,
      selected: previous.selected.map(item => item.update_id === identity ? { ...item, summary } : item) }))
  }
  function changeAnchor() {
    if (!locked && allowNetworkNavigation()) { setDraft(empty); setAnchorId(''); setPage(1) }
  }
  function submit() {
    if (!fresh || locked || !recipient || !ready || draft.expiry === '') return
    mutation.send({ operation: 'create', submission_id: crypto.randomUUID(), input: { source_kind: source.kind,
      source_id: source.id, anchor_project_id: anchorId, recipient_user_code: recipient.user_code,
      label: draft.label, expires_in_days: draft.expiry,
      items: draft.selected.map(item => ({ update_id: item.update_id, summary: item.summary })) } })
  }

  if (mutation.saved) return <NetworkPanel title={t('history_shares.create')} headingId={heading}>
    <p role="status" className="mb-3 text-sm">{t('history_shares.staged')}</p>
    <button type="button" className={NETWORK_BUTTON} onClick={onSent}>{t('history_shares.open_sent')}</button>
  </NetworkPanel>

  return <NetworkPanel title={t('history_shares.create')} headingId={heading}>
    {!denied && <NetworkFreshness read={read} />}
    {denied ? <p role="alert">{t('error_access')}</p> : <>
      {error && <p role="alert">{t(error)}</p>}
      {!error && !visible && <p role="status">{t('history_shares.waiting')}</p>}
      {!anchorId && visible && projects.data && <div className="space-y-3">
        <p className="text-sm text-dim">{t('history_shares.anchor_help')}</p>
        {anchors.length > 0 ? <ul className="space-y-2">
          {anchors.map(anchor => <li key={anchor.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line p-3">
            <span className="min-w-0 break-words text-sm font-semibold">{anchor.title}</span>
            <button type="button" className={NETWORK_BUTTON} disabled={!fresh}
              onClick={() => { setAnchorId(anchor.id); setPage(1) }}>{t('history_shares.choose_anchor')}</button>
          </li>)}
        </ul> : <p role="status">{t('history_shares.no_anchor')}</p>}
        <NetworkPagination page={anchorPage} total={projects.data.total} busy={projects.checking} onChange={setAnchorPage} />
      </div>}
      {anchorId && <button type="button" className={`${NETWORK_BUTTON} mb-3`} disabled={locked} onClick={changeAnchor}>
        {t('history_shares.change_anchor')}
      </button>}
      {data && !locked && <form className="space-y-4" onSubmit={event => { event.preventDefault(); submit() }}>
        <p className="text-sm text-dim">{t('history_shares.draft_help')}</p>
        <fieldset className="space-y-3" disabled={!fresh}>
          <div>
            <label className="block text-sm font-semibold" htmlFor={`${heading}-recipient`}>{t('history_shares.recipient')}</label>
            <select id={`${heading}-recipient`} className={NETWORK_FIELD} value={recipient ? draft.recipient : ''} onChange={event => edit({ recipient: event.target.value })} required>
              <option value="">{t('history_shares.choose_recipient')}</option>
              {data.professionals.map(row => <option key={row.user_code} value={row.user_code}>{row.display_name} · #{row.user_code}</option>)}
            </select>
          </div>
          {!data.professionals.length && <p role="status">{t('history_shares.no_professional')}</p>}
          <div>
            <label className="block text-sm font-semibold" htmlFor={`${heading}-label`}>{t('history_shares.label')}</label>
            <input id={`${heading}-label`} aria-describedby={`${heading}-label-help`} className={NETWORK_FIELD} value={draft.label}
              maxLength={HISTORY_SHARE_MAX_LABEL} required onChange={event => edit({ label: event.target.value })} />
            <p id={`${heading}-label-help`} className="mt-1 text-sm text-dim">{t('history_shares.label_help')}</p>
          </div>
          <div>
            <label className="block text-sm font-semibold" htmlFor={`${heading}-expiry`}>{t('history_shares.expiry')}</label>
            <select id={`${heading}-expiry`} className={NETWORK_FIELD} value={draft.expiry} required onChange={event => {
              edit({ expiry: HISTORY_SHARE_EXPIRIES.find(days => String(days) === event.target.value) ?? '' })
            }}>
              <option value="">{t('history_shares.choose_expiry')}</option>
              {HISTORY_SHARE_EXPIRIES.map(days => <option key={days} value={days}>{t('history_shares.duration', { count: days })}</option>)}
            </select>
          </div>
          <fieldset className="space-y-2">
            <legend className="font-bold">{t('history_shares.completed_entries')}</legend>
            {data.items.map((item, index) => {
              const selected = draft.selected.some(row => row.update_id === item.update_id)
              return <label className="flex min-h-11 min-w-0 items-start gap-3 rounded-lg border border-line p-3 text-sm" key={item.update_id}>
                <input type="checkbox" checked={selected} disabled={!selected && draft.selected.length >= HISTORY_SHARE_MAX_ITEMS}
                  aria-label={t('history_shares.select_entry', { number: (page - 1) * 30 + index + 1, project: item.project_title })}
                  onChange={event => toggle(item, event.target.checked)} />
                <span className="min-w-0 break-words">{item.project_title} · <HistoryWorkMetadata
                  date={item.occurred_on} ids={item.service_category_ids} categories={categories} /></span>
              </label>
            })}
            {!data.items.length && <p role="status">{t('history_shares.no_entries')}</p>}
            {(page > 1 || data.total > page * 30) && <nav aria-label={t('pagination')} className="flex flex-wrap items-center gap-2">
              <button type="button" className={NETWORK_BUTTON} disabled={page <= 1} onClick={() => setPage(page - 1)}>{t('previous')}</button>
              <button type="button" className={NETWORK_BUTTON} disabled={page * 30 >= data.total || page >= 1000}
                onClick={() => setPage(page + 1)}>{t('next')}</button>
            </nav>}
          </fieldset>
          {draft.selected.length > 0 && <fieldset className="space-y-3">
            <legend className="font-bold">{t('history_shares.redacted_summaries')}</legend>
            <p className="text-sm text-dim">{t('history_shares.redaction_help')}</p>
            {draft.selected.map((item, index) => <div className="rounded-lg border border-line p-3" key={item.update_id}>
              <p className="break-words text-sm">{item.project_title} · <HistoryWorkMetadata
                date={item.occurred_on} ids={item.service_category_ids} categories={categories} /></p>
              <label className="mt-2 block text-sm font-semibold">{t('history_shares.summary_for', { number: index + 1 })}
                <textarea className={NETWORK_FIELD} value={item.summary} rows={3} required maxLength={HISTORY_SHARE_MAX_SUMMARY}
                  onChange={event => updateSummary(item.update_id, event.target.value)} />
              </label>
              <button type="button" className={`${NETWORK_BUTTON} mt-2`} onClick={() => toggle(item, false)}>{t('history_shares.remove_entry')}</button>
            </div>)}
          </fieldset>}
          <label className="flex min-h-11 items-start gap-3 text-sm font-semibold">
            <input type="checkbox" checked={draft.reviewed} onChange={event => setDraft(previous => ({ ...previous, reviewed: event.target.checked }))} />
            {t('history_shares.confirm_review')}
          </label>
          <button type="submit" className={NETWORK_PRIMARY} disabled={!ready}>{t('history_shares.submit')}</button>
        </fieldset>
      </form>}
      <ServiceMutationStatus mutation={mutation} />
    </>}
  </NetworkPanel>
}
