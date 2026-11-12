import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAxiosError } from 'axios'
import { addProjectMember, createMilestone, createProject, createProjectUpdate, getMilestones, getNetworkProperties, getPrivateProperties, getPrivateProperty, getPrivatePropertyServiceHistory, getProject, getProjectSubmission, getProjectMembers, getProjects, getProjectUpdates, getPropertyServiceHistory, revokeProjectMember, updateMilestone, updateProject, type CreateProjectInput, type NetworkMilestone, type NetworkProject } from '../../api/propertyNetwork'
import type { ProfessionalOptions } from '../../../../../packages/professionals/src'
import { professionalLabel } from '../../../../../packages/professionals/src'
import { useNetworkRead } from '../../hooks/useNetworkRead'
import { NETWORK_BUTTON, NETWORK_FIELD, NETWORK_PRIMARY, NetworkComments, NetworkComposer, NetworkDate, NetworkFreshness, NetworkPagination, NetworkPanel, PrivatePhotos, networkError, useNetworkDraftGuard, allowNetworkNavigation } from './NetworkShared'
import { DueWorkReminders } from './DueWorkReminders'

type Catalog = ProfessionalOptions & { property_classes?: { id: string; label: { en: string; es: string } }[] }
const PROJECT_STATUSES = ['open', 'in_progress', 'completed', 'archived'] as const
const MILESTONE_STATUSES = ['open', 'in_progress', 'completed', 'cancelled'] as const

export function ProjectList({ options, canCreate, onOpen, initialPropertyId }: { options: Catalog; canCreate: boolean; onOpen: (id: string) => void; initialPropertyId?: string }) {
  const { t, i18n } = useTranslation('property_network')
  const [page, setPage] = useState(1), [creating, setCreating] = useState(!!initialPropertyId), [revision, setRevision] = useState(0)
  const read = useNetworkRead(`projects:${page}:${revision}`, signal => getProjects({ page }, signal))
  const data = read.data, busy = read.checking, error = read.error ? networkError(read.error) : ''
  return <div className="space-y-5"><div className="flex flex-wrap justify-between gap-3"><p className="max-w-3xl text-sm leading-relaxed text-ink2">{t('projects_intro')}</p><button className={NETWORK_PRIMARY} onClick={() => { if (!creating || allowNetworkNavigation()) setCreating(value => !value) }} aria-expanded={creating}>{t(creating ? 'close_form' : 'create_project')}</button></div><NetworkFreshness read={{ ...read, refresh: () => setRevision(value => value+1) }} />
    {creating ? <ProjectCreate options={options} canUseListings={canCreate} initialPropertyId={initialPropertyId} onCreated={onOpen} /> : null}
    {busy ? <p role="status">{t('loading')}</p> : null}{error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}
    {data ? <><div className="grid gap-4 md:grid-cols-2">{data.items.map(project => <article key={project.id} className="min-w-0 rounded-2xl border border-line bg-paper p-5"><p className="text-xs font-semibold text-sea">{project.property_title ?? project.listing_title}</p><h2 className="mt-2 break-words text-xl font-bold text-ink">{project.title}</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink2 line-clamp-3">{project.summary}</p><p className="mt-3 text-xs text-dim">{t(`statuses.${project.status}`)} · {t(project.property_id && project.my_role === 'owner' ? 'registry.project_custodian' : `roles.${project.my_role}`)}</p><p className="mt-2 text-sm font-semibold text-ink">{project.progress.percent === null ? t('no_milestones') : t('progress', { completed: project.progress.completed, total: project.progress.total, percent: project.progress.percent.toLocaleString(i18n.language) })}</p><button className={NETWORK_BUTTON+' mt-4'} onClick={() => onOpen(project.id)}>{t('open_project')}</button></article>)}</div>{!data.items.length ? <p className="rounded-xl bg-paper p-5 text-sm text-dim">{t('projects_empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={busy} onChange={setPage} /></> : null}
  </div>
}

export function ProjectCreate({ options, onCreated, canUseListings = true, initialPropertyId }: { options: Catalog; onCreated: (id: string) => void; canUseListings?: boolean; initialPropertyId?: string }) {
  const { t, i18n } = useTranslation('property_network')
  const [source, setSource] = useState<'listing' | 'private'>(initialPropertyId || !canUseListings ? 'private' : 'listing'), [page, setPage] = useState(1)
  const [listingId, setListingId] = useState(''), [propertyId, setPropertyId] = useState(initialPropertyId ?? ''), [title, setTitle] = useState(''), [summary, setSummary] = useState(''), [propertyClass, setPropertyClass] = useState(''), [categories, setCategories] = useState<string[]>([])
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [pending, setPending] = useState<CreateProjectInput | null>(null), [savedId, setSavedId] = useState(''), [checkedAbsent, setCheckedAbsent] = useState(false)
  const key = useRef(crypto.randomUUID()), alive = useRef(true), request = useRef<AbortController | null>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort() } }, [])
  const listings = useNetworkRead(`project-listings:${page}`, signal => getNetworkProperties({ page }, signal), source === 'listing' && canUseListings)
  const registry = useNetworkRead(`project-registry:${page}`, signal => getPrivateProperties({ page, status: 'active' }, signal), source === 'private')
  const initial = useNetworkRead(`project-initial-registry:${initialPropertyId ?? ''}`, signal => getPrivateProperty(initialPropertyId!, signal), source === 'private' && !!initialPropertyId)
  const selected = registry.data?.items.find(item => item.id === propertyId) ?? (initial.data?.id === propertyId ? initial.data : undefined)
  useEffect(() => { if (selected && !propertyClass && !pending) setPropertyClass(selected.property_class) }, [selected, propertyClass, pending])
  useNetworkDraftGuard(!!title || !!summary || !!listingId || !!propertyId || categories.length > 0 || pending !== null, busy)
  useEffect(() => { if (savedId && !busy) onCreated(savedId) }, [savedId, busy, onCreated])
  const sourceRead = source === 'listing' ? listings : registry
  const sourceError = sourceRead.error ? networkError(sourceRead.error) : initial.error ? networkError(initial.error) : ''
  const access = useRef(true)
  access.current = sourceError !== 'error_access' && error !== 'error_access'
  useEffect(() => { if (!access.current) request.current?.abort() }, [sourceError, error])
  const allowedSource = source === 'listing' ? canUseListings && listings.data?.items.some(item => item.id === listingId) : !!selected?.can_create_project && selected.status === 'active' && selected.property_class === propertyClass
  const blocked = busy || pending !== null
  function completed(id: string) { setPending(null); setTitle(''); setSummary(''); setCategories([]); setListingId(''); setPropertyId(''); setSavedId(id) }
  async function save() {
    if (busy || error === 'error_access' || error === 'error_conflict' || !pending && (!allowedSource || !title.trim() || !propertyClass || !categories.length || sourceRead.stale || sourceRead.error)) return
    if (pending && !pending.property_id) return
    const input = pending ?? { ...(source === 'private' ? { property_id: propertyId, submission_id: key.current } : { listing_id: listingId }), title: title.trim(), summary: summary.trim(), property_class: propertyClass, service_category_ids: categories }
    setPending(input)
    setBusy(true); setError('')
    const controller = new AbortController(); request.current = controller
    try { const project = await createProject(input, controller.signal); if (alive.current && access.current && !controller.signal.aborted) completed(project.id) }
    catch (cause) { if (alive.current && !controller.signal.aborted) setError(networkError(cause)) }
    finally { if (alive.current) setBusy(false) }
  }
  async function checkResult() {
    if (!pending?.submission_id || busy) return
    setBusy(true); setError(''); setCheckedAbsent(false)
    const controller = new AbortController(); request.current = controller
    try { const project = await getProjectSubmission(pending.submission_id, controller.signal); if (project.property_id?.toLowerCase() !== pending.property_id?.toLowerCase()) throw new Error('Unconfirmed project source'); if (alive.current && access.current && !controller.signal.aborted) completed(project.id) }
    catch (cause) { if (alive.current && !controller.signal.aborted) { if (isAxiosError(cause) && cause.response?.status === 404) setCheckedAbsent(true); else setError(networkError(cause)) } }
    finally { if (alive.current) setBusy(false) }
  }
  if (error === 'error_access' || sourceError === 'error_access') return <p role="alert" className="text-sm text-red-800">{t('error_access')}</p>
  return <NetworkPanel title={t('create_project')}><form onSubmit={event => { event.preventDefault(); void save() }} className="space-y-4"><fieldset disabled={blocked} className="space-y-4">
    <p className="text-xs text-dim">{t('project_private')}</p><label className="block text-sm font-semibold">{t('registry.project_source')}<select className={NETWORK_FIELD} value={source} onChange={event => { if (allowNetworkNavigation()) { setSource(event.target.value as typeof source); setPage(1); setListingId(''); setPropertyId(''); setPropertyClass('') } }}><option value="private">{t('registry.private_source')}</option>{canUseListings ? <option value="listing">{t('registry.listing_source')}</option> : null}</select></label>
    <NetworkFreshness read={sourceRead} />{sourceError ? <p role="alert">{t(sourceError)}</p> : null}
    {source === 'listing' ? <label className="block text-sm font-semibold text-ink">{t('property')}<select className={NETWORK_FIELD} value={listingId} required onChange={event => setListingId(event.target.value)}><option value="">{t('choose_property')}</option>{listings.data?.items.map(property => <option key={property.id} value={property.id}>{property.title} · {property.location}</option>)}</select></label> : <><p className="text-xs text-dim">{t('registry.unverified')}</p><label className="block text-sm font-semibold">{t('registry.choose_private')}<select className={NETWORK_FIELD} value={propertyId} required onChange={event => { setPropertyId(event.target.value); setPropertyClass(registry.data?.items.find(item => item.id === event.target.value)?.property_class ?? '') }}><option value="">{t('registry.choose_private')}</option>{registry.data?.items.filter(item => item.can_create_project).map(item => <option key={item.id} value={item.id}>{item.title}</option>)}{initial.data?.can_create_project && !registry.data?.items.some(item => item.id === initial.data!.id) ? <option value={initial.data.id}>{initial.data.title}</option> : null}</select></label>{selected && selected.property_class !== propertyClass ? <button className={NETWORK_BUTTON} type="button" onClick={() => setPropertyClass(selected.property_class)}>{t('registry.use_current_class')}</button> : null}</>}
    {sourceRead.data ? <NetworkPagination page={page} total={sourceRead.data.total} busy={sourceRead.checking} onChange={value => { setPage(value); setListingId(''); setPropertyId(''); setPropertyClass('') }} /> : !sourceError ? <p role="status" className="text-sm">{t('loading_properties')}</p> : null}
    {source === 'private' && registry.data && !registry.data.items.some(item => item.can_create_project) && !initial.data?.can_create_project ? <p role="status" className="text-sm text-dim">{t('registry.no_active_source')}</p> : null}
    <label className="block text-sm font-semibold text-ink">{t('title')}<input className={NETWORK_FIELD} value={title} onChange={event => setTitle(event.target.value)} maxLength={180} required /></label>
    <label className="block text-sm font-semibold text-ink">{t('summary')}<textarea className={NETWORK_FIELD} value={summary} onChange={event => setSummary(event.target.value)} maxLength={4000} rows={3} /></label>
    <label className="block text-sm font-semibold text-ink">{t('property_class')}<select className={NETWORK_FIELD} value={propertyClass} required disabled={source === 'private'} onChange={event => setPropertyClass(event.target.value)}><option value="">{t('choose_class')}</option>{options.property_classes?.map(option => <option key={option.id} value={option.id}>{professionalLabel(option.label, i18n.language)}</option>)}</select></label>
    <fieldset className="rounded-xl border border-line p-3"><legend className="text-sm font-semibold text-ink">{t('services')}</legend><div className="grid max-h-64 gap-2 overflow-y-auto sm:grid-cols-2">{options.categories.map(option => <label key={option.id} className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={categories.includes(option.id)} disabled={!categories.includes(option.id) && categories.length >= 8} onChange={event => setCategories(current => event.target.checked ? [...current, option.id] : current.filter(id => id !== option.id))} />{professionalLabel(option.label, i18n.language)}</label>)}</div></fieldset>
    <button className={NETWORK_PRIMARY} disabled={!allowedSource || !title.trim() || !propertyClass || !categories.length || sourceRead.stale || !!sourceRead.error} type="submit">{t(busy ? 'saving' : 'create_project')}</button>
  </fieldset>{error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}{pending ? <><p role="status" className="text-sm text-amber-950">{t(pending.property_id ? 'registry.uncertain_project' : 'reload_before_new_project')}</p>{pending.property_id ? <div className="flex flex-wrap gap-3"><button type="button" className={NETWORK_BUTTON} disabled={busy} onClick={() => void checkResult()}>{t('registry.check_result')}</button><button type="button" className={NETWORK_BUTTON} disabled={busy || error === 'error_conflict'} onClick={() => void save()}>{t('registry.retry_same_project')}</button>{checkedAbsent ? <p role="status" className="text-sm text-dim">{t('registry.not_found_project')}</p> : null}</div> : null}</> : null}</form></NetworkPanel>
}

export function ProjectDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { t, i18n } = useTranslation('property_network')
  const [manualReload, setManualReload] = useState(0)
  const read = useNetworkRead(`project:${id}:${manualReload}`, signal => getProject(id, signal))
  const project = read.data, error = read.error ? networkError(read.error) : ''
  const [tab, setTab] = useState<'timeline' | 'milestones' | 'members' | 'history' | 'details'>('timeline')
  const reload = read.refresh
  const canReadDueWork = project && (project.my_role === 'owner' || project.my_role === 'professional')
    && (project.status === 'open' || project.status === 'in_progress')
  function changeTab(value: typeof tab) { if (window.dispatchEvent(new Event('ildr:before-dashboard-navigation', { cancelable: true }))) setTab(value) }
  return <div className="space-y-5"><div className="flex flex-wrap gap-3"><button className={NETWORK_BUTTON} onClick={onBack}>{t('back_projects')}</button><button className={NETWORK_BUTTON} onClick={() => { if (allowNetworkNavigation()) setManualReload(value => value+1) }}>{t('reload')}</button></div><NetworkFreshness read={read} />{error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}{!project ? !error ? <p role="status">{t('loading')}</p> : null : <>
    <header className="rounded-2xl bg-ink p-5 text-white"><p className="text-xs text-white/70">{project.property_title ?? project.listing_title}</p><h1 className="mt-2 break-words text-2xl font-bold">{project.title}</h1><p className="mt-3 whitespace-pre-wrap break-words text-sm text-white/80">{project.summary}</p><p className="mt-4 text-sm font-semibold">{t(`statuses.${project.status}`)} · {project.progress.percent === null ? t('no_milestones') : t('progress', { completed: project.progress.completed, total: project.progress.total, percent: project.progress.percent.toLocaleString(i18n.language) })}</p><p className="mt-2 text-xs text-white/65">{t('project_private')}</p>{project.property_id ? <p className="mt-2 text-xs text-white/65">{t('registry.unverified')}</p> : null}</header>
    <nav aria-label={t('project_tabs')} className="flex flex-wrap gap-2">{(['timeline', 'milestones', 'members', 'history', 'details'] as const).map(value => <button className={tab === value ? NETWORK_PRIMARY : NETWORK_BUTTON} key={value} aria-current={tab === value ? 'page' : undefined} onClick={() => changeTab(value)}>{t(`tabs.${value}`)}</button>)}</nav>
    {tab === 'timeline' ? <>
      {canReadDueWork ? <DueWorkReminders key={`${project.id}:${project.my_role}`} projectId={project.id}
        parentFresh={!read.stale && !read.checking && !read.error} /> : null}
      <ProjectTimeline key={project.id} project={project} />
    </> : null}
    {tab === 'milestones' ? <ProjectMilestones key={project.id} project={project} onChanged={reload} /> : null}
    {tab === 'members' ? <ProjectMembers key={project.id} project={project} onChanged={reload} /> : null}
    {tab === 'history' ? project.property_id ? <ServiceHistory key={`private:${project.property_id}`} propertyId={project.property_id} /> : project.listing_id ? <ServiceHistory key={`listing:${project.listing_id}`} listingId={project.listing_id} /> : null : null}
    {tab === 'details' ? <ProjectEdit key={project.id} project={project} onChanged={reload} /> : null}
  </>}</div>
}

function ProjectEdit({ project, onChanged }: { project: NetworkProject; onChanged: () => void }) {
  const { t } = useTranslation('property_network')
  const [title, setTitle] = useState(project.title), [summary, setSummary] = useState(project.summary), [status, setStatus] = useState(project.status)
  const [baseline, setBaseline] = useState(project)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const dirty = title !== baseline.title || summary !== baseline.summary || status !== baseline.status
  useNetworkDraftGuard(dirty, busy)
  useEffect(() => { if (!busy && !dirty && project.version > baseline.version) { setTitle(project.title); setSummary(project.summary); setStatus(project.status); setBaseline(project) } }, [project, baseline.version, busy, dirty])
  async function save() { setBusy(true); setError(''); try { const saved = await updateProject(project.id, { expected_version: baseline.version, title, summary, status }); setTitle(saved.title); setSummary(saved.summary); setStatus(saved.status); setBaseline(saved); onChanged() } catch (cause) { setError(networkError(cause)) } finally { setBusy(false) } }
  return <NetworkPanel title={t('project_details')}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void save() }}><fieldset className="space-y-4" disabled={!project.can_manage || busy || !!error}><label className="block text-sm font-semibold">{t('title')}<input className={NETWORK_FIELD} value={title} maxLength={180} required onChange={event => setTitle(event.target.value)} /></label><label className="block text-sm font-semibold">{t('summary')}<textarea className={NETWORK_FIELD} value={summary} rows={4} maxLength={4000} onChange={event => setSummary(event.target.value)} /></label><label className="block text-sm font-semibold">{t('status')}<select className={NETWORK_FIELD} value={status} onChange={event => setStatus(event.target.value as typeof status)}>{PROJECT_STATUSES.map(value => <option key={value} value={value}>{t(`statuses.${value}`)}</option>)}</select></label>{project.can_manage ? <button type="submit" className={NETWORK_PRIMARY}>{t(busy ? 'saving' : 'save_changes')}</button> : null}</fieldset>{!project.can_manage ? <p className="text-xs text-dim">{t('read_only')}</p> : null}{error ? <p role="alert" className="text-sm text-red-800">{t(error)} {t('reload_to_continue')}</p> : null}</form></NetworkPanel>
}

function ProjectTimeline({ project }: { project: NetworkProject }) {
  const { t } = useTranslation('property_network')
  const [page, setPage] = useState(1)
  const read = useNetworkRead(`timeline:${project.id}:${page}`, signal => getProjectUpdates(project.id, { page }, signal))
  const data = read.data, error = read.error ? networkError(read.error) : ''
  const [correction, setCorrection] = useState<string | null>(null)
  function chooseCorrection(value: string | null) { if (allowNetworkNavigation()) setCorrection(value) }
  return <NetworkPanel title={t('timeline')}><NetworkFreshness read={read} /><p className="text-xs text-dim">{t('timeline_note')}</p>{project.can_update ? <><p className="mt-4 text-sm font-bold text-ink">{t(correction ? 'add_correction' : 'add_progress')}</p>{correction ? <button type="button" className={NETWORK_BUTTON} onClick={() => chooseCorrection(null)}>{t('cancel_correction')}</button> : null}<NetworkComposer key={correction ?? 'new'} projectId={project.id} dated photos submit={input => createProjectUpdate(project.id, { ...input, correction_of: correction })} onSaved={() => { setCorrection(null); setPage(1); read.refresh() }} /></> : <p className="mt-4 text-sm text-dim">{t('read_only')}</p>}
    {error ? <p role="alert" className="mt-4 text-sm text-red-800">{t(error)}</p> : null}{!data ? !error ? <p role="status" className="mt-4">{t('loading')}</p> : null : <><ol className="mt-5 space-y-5">{data.items.map(entry => <li key={entry.id} className="min-w-0 border-l-2 border-sea/30 pl-4"><div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-bold text-ink">{entry.author.display_name} · {t(`event_kinds.${entry.kind}`)}</p><p className="text-xs text-dim"><NetworkDate value={entry.occurred_on} /></p></div><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink2">{entry.body}</p>{entry.correction_of ? <p className="mt-2 text-xs text-amber-950">{t('correction_note')}</p> : null}<PrivatePhotos ids={entry.media_ids} />{project.can_update && entry.kind !== 'milestone' ? <button className={NETWORK_BUTTON+' mt-3'} onClick={() => chooseCorrection(entry.id)}>{t('correct_entry')}</button> : null}<NetworkComments recordId={entry.id} kind="update" canWrite={project.can_update} mediaIds={entry.media_ids} /></li>)}</ol>{!data.items.length ? <p className="mt-4 text-sm text-dim">{t('timeline_empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={false} onChange={setPage} /></>}
  </NetworkPanel>
}

function MilestoneEditor({ item, project, onChanged }: { item?: NetworkMilestone; project: NetworkProject; onChanged: () => void }) {
  const { t } = useTranslation('property_network')
  const [title, setTitle] = useState(item?.title ?? ''), [due, setDue] = useState(item?.due_date ?? ''), [status, setStatus] = useState(item?.status ?? 'open')
  const [baseline, setBaseline] = useState(item)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const dirty = title !== (baseline?.title ?? '') || due !== (baseline?.due_date ?? '') || status !== (baseline?.status ?? 'open')
  useNetworkDraftGuard(dirty, busy)
  useEffect(() => { if (!busy && !dirty && item && item.version > (baseline?.version ?? 0)) { setTitle(item.title); setDue(item.due_date ?? ''); setStatus(item.status); setBaseline(item) } }, [item, baseline?.version, busy, dirty])
  async function save() { setBusy(true); setError(''); try { if (item && baseline) { const saved = await updateMilestone(item.id, { expected_version: baseline.version, title, due_date: due || null, status }); setTitle(saved.title); setDue(saved.due_date ?? ''); setStatus(saved.status); setBaseline(saved) } else { await createMilestone(project.id, { title, due_date: due || null }); setTitle(''); setDue(''); setStatus('open') } onChanged() } catch (cause) { setError(networkError(cause)) } finally { setBusy(false) } }
  return <form className="space-y-3 rounded-xl border border-line p-4" onSubmit={event => { event.preventDefault(); void save() }}><fieldset disabled={busy || !!error || !project.can_manage} className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('milestone_title')}<input className={NETWORK_FIELD} value={title} required maxLength={180} onChange={event => setTitle(event.target.value)} /></label><label className="block text-sm font-semibold">{t('due_date')}<input type="date" className={NETWORK_FIELD} value={due} onChange={event => setDue(event.target.value)} /></label>{item ? <label className="block text-sm font-semibold">{t('status')}<select className={NETWORK_FIELD} value={status} onChange={event => setStatus(event.target.value as typeof status)}>{MILESTONE_STATUSES.map(value => <option key={value} value={value}>{t(`statuses.${value}`)}</option>)}</select></label> : null}{project.can_manage ? <button className={NETWORK_PRIMARY+' self-end'} type="submit">{t(busy ? 'saving' : item ? 'save_changes' : 'add_milestone')}</button> : null}</fieldset>{error ? <p role="alert" className="text-sm text-red-800">{t(error)} {t('reload_to_continue')}</p> : null}</form>
}
function ProjectMilestones({ project, onChanged }: { project: NetworkProject; onChanged: () => void }) {
  const { t } = useTranslation('property_network')
  const [page, setPage] = useState(1)
  const read = useNetworkRead(`milestones:${project.id}:${page}`, signal => getMilestones(project.id, { page }, signal))
  const data = read.data, error = read.error ? networkError(read.error) : ''
  const changed = () => { read.refresh(); onChanged() }
  return <NetworkPanel title={t('milestones')}><NetworkFreshness read={read} /><p className="mb-4 text-xs text-dim">{t('milestones_note')}</p>{project.can_manage ? <MilestoneEditor project={project} onChanged={changed} /> : null}{error ? <p role="alert">{t(error)}</p> : null}{!data ? !error ? <p role="status">{t('loading')}</p> : null : <><div className="mt-4 space-y-3">{data.items.map(item => <MilestoneEditor key={item.id} item={item} project={project} onChanged={changed} />)}</div>{!data.items.length ? <p className="mt-4 text-sm text-dim">{t('milestones_empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={false} onChange={setPage} /></>}</NetworkPanel>
}
function ProjectMembers({ project, onChanged }: { project: NetworkProject; onChanged: () => void }) {
  const { t } = useTranslation('property_network')
  const [code, setCode] = useState(''), [role, setRole] = useState<'manager' | 'professional' | 'viewer'>('professional')
  const [writeError, setError] = useState(''), [busy, setBusy] = useState(false)
  const read = useNetworkRead(`members:${project.id}`, signal => getProjectMembers(project.id, signal))
  const data = read.data, error = writeError || (read.error ? networkError(read.error) : '')
  async function change(action: 'add' | 'revoke', userCode: number) { setBusy(true); setError(''); try { if (action === 'add') await addProjectMember(project.id, { expected_version: project.version, user_code: userCode, role }); else await revokeProjectMember(project.id, userCode, project.version); read.refresh(); onChanged() } catch (cause) { setError(networkError(cause)) } finally { setBusy(false) } }
  return <NetworkPanel title={t('members')}><NetworkFreshness read={read} /><p className="mb-4 text-xs text-dim">{t('members_note')}</p>{data ? <ul className="space-y-3">{data.map((member, index) => <li key={member.user_code ?? `former:${index}`} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line p-3"><div><p className="text-sm font-semibold text-ink">{member.display_name}</p><p className="text-xs text-dim">{member.user_code ? `#${member.user_code} · ` : ''}{t(project.property_id && member.role === 'owner' ? 'registry.project_custodian' : `roles.${member.role}`)} · {t(member.active ? 'active' : 'revoked')}</p></div>{project.can_assign && member.active && member.role !== 'owner' && member.user_code !== null ? <button className={NETWORK_BUTTON} disabled={busy || !!error} onClick={() => void change('revoke', member.user_code!)}>{t('revoke_access')}</button> : null}</li>)}</ul> : !error ? <p role="status">{t('loading')}</p> : null}
    {project.can_assign ? <form className="mt-5 space-y-3" onSubmit={event => { event.preventDefault(); void change('add', Number(code)) }}><fieldset disabled={busy || !!error} className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-semibold">{t('member_code')}<input type="number" min="1" max="999999999" step="1" required className={NETWORK_FIELD} value={code} onChange={event => setCode(event.target.value)} /></label><label className="block text-sm font-semibold">{t('member_role')}<select className={NETWORK_FIELD} value={role} onChange={event => setRole(event.target.value as typeof role)}>{(['professional', 'manager', 'viewer'] as const).map(value => <option key={value} value={value}>{t(`roles.${value}`)}</option>)}</select></label><button className={NETWORK_PRIMARY} type="submit">{t(busy ? 'saving' : 'add_member')}</button></fieldset></form> : null}{error ? <p role="alert" className="mt-4 text-sm text-red-800">{t(error)} {t('reload_to_continue')}</p> : null}
  </NetworkPanel>
}
export function ServiceHistory({ listingId, propertyId }: { listingId?: string; propertyId?: string }) {
  const { t } = useTranslation('property_network')
  const [page, setPage] = useState(1)
  const read = useNetworkRead(`history:${propertyId ? 'private' : 'listing'}:${propertyId ?? listingId}:${page}`, signal => propertyId ? getPrivatePropertyServiceHistory(propertyId, { page }, signal) : getPropertyServiceHistory(listingId!, { page }, signal))
  const data = read.data, error = read.error ? networkError(read.error) : ''
  return <NetworkPanel title={t('service_history')}><NetworkFreshness read={read} /><p className="mb-4 text-xs text-dim">{t(propertyId ? 'registry.history_note' : 'service_history_note')}</p>{error ? <p role="alert">{t(error)}</p> : null}{!data ? !error ? <p role="status">{t('loading')}</p> : null : <><ol className="space-y-4">{data.items.map(item => <li key={item.id} className="rounded-xl border border-line p-4"><p className="font-semibold text-ink">{item.project_title}</p><p className="mt-1 text-xs text-dim"><NetworkDate value={item.occurred_on} /></p><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink2">{item.body}</p><PrivatePhotos ids={item.media_ids} /></li>)}</ol>{!data.items.length ? <p className="text-sm text-dim">{t('history_empty')}</p> : null}<NetworkPagination page={page} total={data.total} busy={false} onChange={setPage} /></>}</NetworkPanel>
}
