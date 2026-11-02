import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAxiosError } from 'axios'
import { PRIVATE_PROPERTY_CLASSES, createPrivateProperty, getPrivateProperties, getPrivateProperty, getPrivatePropertySubmission, updatePrivateProperty, type CreatePrivatePropertyInput, type PrivateProperty, type PrivatePropertyFields, type PrivatePropertyStatus } from '../../api/propertyNetwork'
import { professionalLabel, type ProfessionalOptions } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkFreshness, NetworkPagination, NetworkPanel, allowNetworkNavigation, networkError, useNetworkDraftGuard } from './NetworkShared'
import { ServiceHistory } from './ProjectWorkspace'

type Draft = { title: string; location: string; address: string; property_class: string; relationship: string; notes: string }
const emptyDraft = (): Draft => ({ title: '', location: '', address: '', property_class: '', relationship: '', notes: '' })
const draftOf = (record: PrivateProperty): Draft => ({ title: record.title, location: record.location, address: record.address, property_class: record.property_class, relationship: record.relationship, notes: record.notes })
const equalDraft = (left: Draft, right: Draft) => Object.keys(left).every(key => left[key as keyof Draft] === right[key as keyof Draft])
function validDraft(draft: Draft) { return !!draft.title.trim() && !!draft.location.trim() && PRIVATE_PROPERTY_CLASSES.some(id => id === draft.property_class) && ['owner', 'manager', 'seller'].includes(draft.relationship) }
function fields(draft: Draft): PrivatePropertyFields {
  return { ...draft, title: draft.title.trim(), location: draft.location.trim(), property_class: draft.property_class as PrivatePropertyFields['property_class'], relationship: draft.relationship as PrivatePropertyFields['relationship'] }
}
function PropertyFields({ draft, setDraft, options }: { draft: Draft; setDraft: (value: Draft) => void; options: ProfessionalOptions }) {
  const { t, i18n } = useTranslation('property_network')
  const change = (name: keyof Draft, value: string) => setDraft({ ...draft, [name]: value })
  return <>
    <label className="block text-sm font-semibold">{t('registry.title')}<input className={NETWORK_FIELD} value={draft.title} maxLength={180} required onChange={event => change('title', event.target.value)} /></label>
    <label className="block text-sm font-semibold">{t('registry.location')}<input className={NETWORK_FIELD} value={draft.location} maxLength={180} required onChange={event => change('location', event.target.value)} /></label>
    <label className="block text-sm font-semibold">{t('registry.address')}<textarea className={NETWORK_FIELD} value={draft.address} maxLength={500} rows={2} onChange={event => change('address', event.target.value)} /></label>
    <div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('property_class')}<select className={NETWORK_FIELD} value={draft.property_class} required onChange={event => change('property_class', event.target.value)}><option value="">{t('choose_class')}</option>{options.property_classes?.filter(option => PRIVATE_PROPERTY_CLASSES.some(id => id === option.id)).map(option => <option key={option.id} value={option.id}>{professionalLabel(option.label, i18n.language)}</option>)}</select></label>
      <label className="block text-sm font-semibold">{t('registry.relationship')}<select className={NETWORK_FIELD} value={draft.relationship} required onChange={event => change('relationship', event.target.value)}><option value="">{t('registry.choose_relationship')}</option>{(['owner', 'manager', 'seller'] as const).map(value => <option key={value} value={value}>{t(`registry.relationships.${value}`)}</option>)}</select></label></div>
    <label className="block text-sm font-semibold">{t('registry.notes')}<textarea className={NETWORK_FIELD} value={draft.notes} maxLength={4000} rows={4} onChange={event => change('notes', event.target.value)} /></label>
  </>
}

export function PrivatePropertyList({ options, onOpen }: { options: ProfessionalOptions; onOpen: (id: string) => void }) {
  const { t } = useTranslation('property_network')
  const [page, setPage] = useState(1), [status, setStatus] = useState<PrivatePropertyStatus | 'all'>('all'), [creating, setCreating] = useState(false), [revision, setRevision] = useState(0)
  const [lastCreateCapability, setLastCreateCapability] = useState(false)
  const read = useNetworkRead(`registry:${status}:${page}:${revision}`, signal => getPrivateProperties({ page, status }, signal))
  const data = read.data, error = read.error ? networkError(read.error) : ''
  useEffect(() => { if (error === 'error_access') setLastCreateCapability(false); else if (data) setLastCreateCapability(data.can_create) }, [data, error])
  const canCreate = data?.can_create ?? (error === 'error_access' ? false : lastCreateCapability)
  return <div className="space-y-5"><NetworkPanel title={t('registry.heading')}><p className="text-sm leading-relaxed text-ink2">{t('registry.intro')}</p><p className="mt-3 text-xs text-dim">{t('registry.unverified')}</p>
    <div className="mt-4 flex flex-wrap items-end justify-between gap-3"><label className="text-sm font-semibold">{t('registry.filter')}<select className={NETWORK_FIELD} value={status} onChange={event => { if (allowNetworkNavigation()) { setStatus(event.target.value as typeof status); setPage(1) } }}>{(['all', 'active', 'archived'] as const).map(value => <option key={value} value={value}>{t(`registry.statuses.${value}`)}</option>)}</select></label>{canCreate ? <button className={NETWORK_PRIMARY} aria-expanded={creating} onClick={() => { if (!creating || allowNetworkNavigation()) setCreating(value => !value) }}>{t(creating ? 'registry.close_form' : 'registry.add')}</button> : null}</div>
  </NetworkPanel>
    {data && !data.can_create ? <p role="status" className="text-sm text-dim">{t('registry.creation_unavailable')}</p> : null}
    {creating && canCreate ? <PrivatePropertyCreate options={options} onCreated={onOpen} /> : null}
    <NetworkFreshness read={{ ...read, refresh: () => setRevision(value => value+1) }} />{error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}
    {data ? <><div className="grid gap-4 md:grid-cols-2">{data.items.map(record => <article className="min-w-0 rounded-2xl border border-line bg-paper p-5" key={record.id}><p className="text-xs font-semibold text-sea">{t(`registry.statuses.${record.status}`)}</p><h2 className="mt-2 break-words text-xl font-bold">{record.title}</h2><p className="mt-2 break-words text-sm text-ink2">{record.location}</p><button className={NETWORK_BUTTON+' mt-4'} onClick={() => { if (allowNetworkNavigation()) onOpen(record.id) }}>{t('registry.open')}</button></article>)}</div>{!data.items.length ? <p className="rounded-xl bg-paper p-5 text-sm text-dim">{t('registry.empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={read.checking} onChange={setPage} /></> : !error ? <p role="status">{t('loading')}</p> : null}
  </div>
}

export function PrivatePropertyCreate({ options, onCreated }: { options: ProfessionalOptions; onCreated: (id: string) => void }) {
  const { t } = useTranslation('property_network')
  const [draft, setDraft] = useState<Draft>(emptyDraft), [acknowledged, setAcknowledged] = useState(false)
  const [pending, setPending] = useState<CreatePrivatePropertyInput | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [checkedAbsent, setCheckedAbsent] = useState(false), [savedId, setSavedId] = useState('')
  const key = useRef(crypto.randomUUID()), alive = useRef(true), request = useRef<AbortController | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort() } }, [])
  useNetworkDraftGuard(Object.values(draft).some(Boolean) || acknowledged || pending !== null, busy)
  useEffect(() => { if (savedId && !busy) onCreated(savedId) }, [savedId, busy, onCreated])
  function complete(record: PrivateProperty) { setDraft(emptyDraft()); setAcknowledged(false); setPending(null); setError(''); setSavedId(record.id) }
  async function save() {
    if (busy || (!pending && (!validDraft(draft) || !acknowledged)) || error === 'error_access' || error === 'error_conflict') return
    const input = pending ?? { ...fields(draft), submission_id: key.current, authority_acknowledged: true }
    setPending(input); setBusy(true); setError(''); setCheckedAbsent(false)
    const controller = new AbortController(); request.current = controller
    try { const record = await createPrivateProperty(input, controller.signal); if (alive.current && !controller.signal.aborted) complete(record) }
    catch (cause) { if (alive.current) setError(networkError(cause)) }
    finally { if (alive.current) setBusy(false) }
  }
  async function checkResult() {
    if (!pending || busy) return
    setBusy(true); setError(''); setCheckedAbsent(false)
    const controller = new AbortController(); request.current = controller
    try { const record = await getPrivatePropertySubmission(pending.submission_id, controller.signal); if (alive.current && !controller.signal.aborted) complete(record) }
    catch (cause) { if (alive.current && !controller.signal.aborted) { if (isAxiosError(cause) && cause.response?.status === 404) setCheckedAbsent(true); else setError(networkError(cause)) } }
    finally { if (alive.current) setBusy(false) }
  }
  if (error === 'error_access') return <p role="alert" className="rounded-xl bg-paper p-5 text-sm text-red-800">{t('error_access')}</p>
  return <NetworkPanel title={t('registry.add')}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void save() }}><p className="text-xs text-dim">{t('registry.unverified')}</p><fieldset className="space-y-4" disabled={busy || pending !== null}><PropertyFields draft={draft} setDraft={setDraft} options={options} /><label className="flex min-h-11 items-start gap-3 text-sm"><input className="mt-1" type="checkbox" checked={acknowledged} required onChange={event => setAcknowledged(event.target.checked)} />{t('registry.authority')}</label></fieldset>
    {error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}{pending ? <><p role="status" className="text-sm text-amber-950">{t('registry.uncertain')}</p><button type="button" className={NETWORK_BUTTON} disabled={busy} onClick={() => void checkResult()}>{t('registry.check_result')}</button>{checkedAbsent ? <p role="status" className="text-sm text-dim">{t('registry.not_found_submission')}</p> : null}</> : null}
    <button className={NETWORK_PRIMARY} type="submit" disabled={busy || error === 'error_conflict' || (!pending && (!validDraft(draft) || !acknowledged))}>{t(busy ? 'saving' : pending ? 'registry.retry_same' : 'registry.register')}</button>
  </form></NetworkPanel>
}

export function PrivatePropertyDetail({ id, options, onBack, onCreateProject }: { id: string; options: ProfessionalOptions; onBack: () => void; onCreateProject: (id: string) => void }) {
  const { t } = useTranslation('property_network')
  const [revision, setRevision] = useState(0), [baseline, setBaseline] = useState<PrivateProperty | null>(null), [draft, setDraft] = useState<Draft>(emptyDraft)
  const [busy, setBusy] = useState(false), [writeError, setWriteError] = useState(''), [requiresRefresh, setRequiresRefresh] = useState(false), [refreshRequested, setRefreshRequested] = useState(false), [accessLost, setAccessLost] = useState(false)
  const read = useNetworkRead(`registry-detail:${id}:${revision}`, signal => getPrivateProperty(id, signal))
  const record = read.data, readError = read.error ? networkError(read.error) : '', denied = accessLost || readError === 'error_access'
  const dirty = baseline !== null && !equalDraft(draft, draftOf(baseline))
  const alive = useRef(true), request = useRef<AbortController | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort() } }, [])
  useNetworkDraftGuard(dirty, busy)
  useEffect(() => {
    if (denied) { request.current?.abort(); setBaseline(null); setDraft(emptyDraft()); return }
    if (!record || busy || read.stale || read.error) return
    if (!baseline || refreshRequested) {
      if (!dirty) setDraft(draftOf(record))
      setBaseline(record); setRefreshRequested(false); setRequiresRefresh(false); setWriteError('')
    } else if (!dirty && !requiresRefresh && record.version > baseline.version) { setBaseline(record); setDraft(draftOf(record)) }
  }, [record, read.stale, read.error, busy, denied, baseline, dirty, refreshRequested, requiresRefresh])
  function refresh() { if (!busy) { setRefreshRequested(true); setAccessLost(false); setRevision(value => value+1) } }
  async function save(archive = false) {
    if (busy || denied || requiresRefresh || !record?.can_manage || !baseline || read.stale || record.version !== baseline.version || !validDraft(draft) || archive && dirty) return
    if (archive && !window.confirm(t('registry.archive_confirm'))) return
    const controller = new AbortController(); request.current = controller; setBusy(true); setWriteError('')
    try {
      const saved = await updatePrivateProperty(id, { ...fields(draft), expected_version: baseline.version, status: archive ? 'archived' : baseline.status }, controller.signal)
      if (alive.current && !controller.signal.aborted) { setBaseline(saved); setDraft(draftOf(saved)); read.refresh() }
    } catch (cause) { if (alive.current && !controller.signal.aborted) { const code = networkError(cause); setWriteError(code); setRequiresRefresh(true); if (code === 'error_access') setAccessLost(true) } }
    finally { if (alive.current) setBusy(false) }
  }
  const writable = !busy && !denied && !requiresRefresh && !!record?.can_manage && baseline !== null && record.version === baseline.version && !read.stale && !read.error
  const currentRecord = record && baseline && baseline.version > record.version ? baseline : record
  return <div className="space-y-5"><div className="flex flex-wrap gap-3"><button className={NETWORK_BUTTON} onClick={() => { if (allowNetworkNavigation()) onBack() }}>{t('registry.back')}</button><button className={NETWORK_BUTTON} disabled={busy} onClick={refresh}>{t('registry.refresh_keep_draft')}</button></div><NetworkFreshness read={{ ...read, refresh }} />
    {denied ? <p role="alert" className="text-sm text-red-800">{t('error_access')}</p> : <>
      {writeError || readError ? <p role="alert" className="text-sm text-red-800">{t(writeError || readError)}</p> : null}
      {requiresRefresh ? <p role="status" className="text-sm text-amber-950">{t('registry.refresh_required')}</p> : null}
      {currentRecord && !requiresRefresh ? <header className="rounded-2xl bg-ink p-5 text-white"><p className="text-xs text-white/70">{t(`registry.statuses.${currentRecord.status}`)}</p><h1 className="mt-2 break-words text-2xl font-bold">{currentRecord.title}</h1><p className="mt-3 text-sm text-white/80">{t('registry.unverified')}</p>{currentRecord.can_create_project ? <button className={NETWORK_BUTTON+' mt-4'} disabled={dirty || busy} onClick={() => { if (allowNetworkNavigation()) onCreateProject(currentRecord.id) }}>{t('registry.create_project')}</button> : <p className="mt-4 text-sm">{t(currentRecord.status === 'archived' ? 'registry.archived_note' : 'registry.project_unavailable')}</p>}</header> : !baseline && !readError ? <p role="status">{t('loading')}</p> : null}
      {baseline ? <NetworkPanel title={t('registry.details')}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void save() }}><fieldset className="space-y-4" disabled={!writable}><PropertyFields draft={draft} setDraft={setDraft} options={options} /><button className={NETWORK_PRIMARY} type="submit" disabled={!dirty || !validDraft(draft)}>{t(busy ? 'saving' : 'save_changes')}</button></fieldset>{dirty ? <p className="text-xs text-dim">{t('registry.draft_retained')}</p> : null}{record && record.version !== baseline.version ? <p role="status" className="text-sm text-amber-950">{t('registry.new_version')}</p> : null}</form>{baseline.status === 'active' ? <button className={NETWORK_BUTTON+' mt-5'} disabled={!writable || dirty} onClick={() => void save(true)}>{t('registry.archive')}</button> : null}{dirty ? <p className="mt-2 text-xs text-dim">{t('registry.save_before_archive')}</p> : null}</NetworkPanel> : null}
      {record && !requiresRefresh ? <ServiceHistory propertyId={record.id} /> : null}
    </>}
  </div>
}
