import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { isAxiosError } from 'axios'
import { createPostComment, createUpdateComment, getNetworkMediaBlob, getPostComments, getUpdateComments, hideNetworkComment, uploadNetworkMedia, type NetworkMedia } from '../../api/propertyNetwork'
import { todayInDR } from '../../api/propertyManagement'
import { useNetworkRead, type NetworkRead } from '../../hooks/useNetworkRead'

export const NETWORK_FIELD = 'mt-1 min-h-11 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink disabled:opacity-60'
export const NETWORK_BUTTON = 'min-h-11 rounded-xl border border-line bg-white px-4 py-2 text-sm font-semibold text-ink disabled:opacity-50'
export const NETWORK_PRIMARY = 'min-h-11 rounded-xl bg-sea px-4 py-2 text-sm font-bold text-white disabled:opacity-50'
export function allowNetworkNavigation() { return window.dispatchEvent(new Event('ildr:before-dashboard-navigation', { cancelable: true })) }
export function useNetworkDraftGuard(dirty: boolean, busy = false) {
  const { t } = useTranslation('property_network')
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => { if (dirty || busy) { event.preventDefault(); event.returnValue = '' } }
    const navigate = (event: Event) => { if (busy || dirty && !window.confirm(t('leave_confirm'))) event.preventDefault() }
    window.addEventListener('beforeunload', unload); window.addEventListener('ildr:before-dashboard-navigation', navigate)
    return () => { window.removeEventListener('beforeunload', unload); window.removeEventListener('ildr:before-dashboard-navigation', navigate) }
  }, [dirty, busy, t])
}
export function NetworkPanel({ title, children, headingId }: { title: string; children: ReactNode; headingId?: string }) {
  return <section aria-labelledby={headingId} className="min-w-0 rounded-2xl border border-line bg-paper p-4 sm:p-6"><h2 id={headingId} className="mb-4 text-lg font-bold text-ink">{title}</h2>{children}</section>
}
export function networkError(error: unknown): string {
  if (isAxiosError(error)) {
    if ([401, 403, 404].includes(error.response?.status ?? 0)) return 'error_access'
    if (error.response?.status === 409) return 'error_conflict'
    if (error.response?.status === 503) return 'error_unavailable'
    if (error.response?.status === 422) return 'error_fields'
  }
  return 'error_unknown'
}
export function NetworkDate({ value, showTime = false }: { value: string; showTime?: boolean }) {
  const { i18n } = useTranslation('property_network')
  const date = new Date(value.length === 10 ? value+'T12:00:00Z' : value)
  return <time dateTime={value}>{Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(i18n.language.startsWith('es') ? 'es-DO' : 'en-US', {
    dateStyle: 'medium', timeStyle: showTime ? 'short' : undefined, timeZone: 'America/Santo_Domingo',
  }).format(date) : '—'}</time>
}
export function NetworkFreshness({ read }: { read: Pick<NetworkRead<unknown>, 'checking' | 'checkedAt' | 'stale' | 'refresh'> }) {
  const { t, i18n } = useTranslation('property_network')
  const time = read.checkedAt === null ? '' : new Intl.DateTimeFormat(i18n.language.startsWith('es') ? 'es-DO' : 'en-US', { timeStyle: 'medium', timeZone: 'America/Santo_Domingo' }).format(read.checkedAt)
  return <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-dim"><p aria-live="polite">{read.checkedAt === null ? t('freshness_waiting') : t('freshness_checked', { time })}{read.stale ? ` · ${t('freshness_stale')}` : ''}</p><button type="button" className={NETWORK_BUTTON} disabled={read.checking} onClick={read.refresh}>{t(read.checking ? 'freshness_checking' : 'refresh')}</button></div>
}
export function NetworkPagination({ page, total, busy, onChange }: { page: number; total: number; busy: boolean; onChange: (page: number) => void }) {
  const { t } = useTranslation('property_network')
  return <nav aria-label={t('pagination')} className="mt-4 flex flex-wrap items-center justify-between gap-3"><button type="button" className={NETWORK_BUTTON} disabled={busy || page <= 1} onClick={() => { if (allowNetworkNavigation()) onChange(page-1) }}>{t('previous')}</button><span className="text-xs text-dim">{t('page', { page, pages: Math.max(1, Math.ceil(total/30)), count: total })}</span><button type="button" className={NETWORK_BUTTON} disabled={busy || page*30 >= total || page >= 1000} onClick={() => { if (allowNetworkNavigation()) onChange(page+1) }}>{t('next')}</button></nav>
}
function PrivatePhoto({ id }: { id: string }) {
  const { t } = useTranslation('property_network')
  const [url, setUrl] = useState(''), [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = ''
    setUrl(''); setError(false)
    getNetworkMediaBlob(id, controller.signal).then(blob => {
      if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) }
    }).catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [id])
  return error ? <p role="status" className="rounded-xl border border-line p-4 text-xs text-dim">{t('photo_unavailable')}</p> : url ? <a href={url} target="_blank" rel="noopener noreferrer" aria-label={t('open_photo')} className="rounded-xl border border-line bg-white p-1"><img src={url} alt={t('photo_alt')} className="max-h-80 w-full rounded-lg object-contain" /></a> : <p role="status" className="p-4 text-xs text-dim">{t('photo_loading')}</p>
}
export function PrivatePhotos({ ids }: { ids: string[] }) {
  return ids.length ? <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">{ids.map(id => <PrivatePhoto key={id} id={id} />)}</div> : null
}
export type NetworkSubmission = { submission_id: string; body: string; media_ids: string[]; occurred_on: string }
export function NetworkComposer({ projectId, photos = false, dated = false, maxLength = 4000, submit, onSaved }: { projectId?: string; photos?: boolean; dated?: boolean; maxLength?: number; submit: (input: NetworkSubmission) => Promise<unknown>; onSaved: () => void }) {
  const { t } = useTranslation('property_network')
  const [body, setBody] = useState(''), [date, setDate] = useState(todayInDR)
  const [attachments, setAttachments] = useState<NetworkMedia[]>([])
  const [busy, setBusy] = useState(false), [uploading, setUploading] = useState(false), [error, setError] = useState('')
  const [pending, setPending] = useState<NetworkSubmission | null>(null)
  const submissionId = useRef(crypto.randomUUID())
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const blocked = busy || uploading || pending !== null
  useNetworkDraftGuard(!!body || attachments.length > 0 || pending !== null, busy || uploading)
  async function upload(file?: File) {
    if (!file || blocked || attachments.length >= 6) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5*1024*1024) { setError('photo_invalid'); return }
    setUploading(true); setError('')
    try { const media = await uploadNetworkMedia(file, projectId); if (alive.current) setAttachments(current => [...current, media]) }
    catch (cause) { if (alive.current) setError(networkError(cause)) }
    finally { if (alive.current) setUploading(false) }
  }
  async function save() {
    if (busy || uploading || (!pending && !body.trim())) return
    const input = pending ?? { submission_id: submissionId.current, body: body.trim(), media_ids: attachments.map(item => item.id), occurred_on: date }
    setBusy(true); setError('')
    try {
      await submit(input)
      if (alive.current) { setBody(''); setAttachments([]); setPending(null); submissionId.current = crypto.randomUUID(); setDate(todayInDR()); onSaved() }
    } catch (cause) {
      if (alive.current) { setPending(input); setError(networkError(cause)) }
    } finally { if (alive.current) setBusy(false) }
  }
  return <form onSubmit={event => { event.preventDefault(); void save() }} className="mt-4 space-y-3 rounded-xl border border-line bg-white p-4">
    <label className="block text-sm font-semibold text-ink">{t('write_update')}<textarea className={NETWORK_FIELD} rows={3} maxLength={maxLength} disabled={blocked} value={body} onChange={event => setBody(event.target.value)} required /></label>
    {dated ? <label className="block text-sm font-semibold text-ink">{t('occurred_on')}<input type="date" max={todayInDR()} className={NETWORK_FIELD} value={date} disabled={blocked} required onChange={event => setDate(event.target.value)} /></label> : null}
    {photos ? <><label className="block text-sm font-semibold text-ink">{t('attach_photos')}<input className="mt-2 block max-w-full text-sm" type="file" accept="image/jpeg,image/png,image/webp" disabled={blocked || attachments.length >= 6} onChange={event => { void upload(event.target.files?.[0]); event.currentTarget.value = '' }} /></label><p className="text-xs text-dim">{t('photo_help')}</p>{uploading ? <p role="status" className="text-sm text-dim">{t('uploading')}</p> : null}{attachments.length ? <><PrivatePhotos ids={attachments.map(item => item.id)} /><div className="flex flex-wrap gap-2">{attachments.map((item, index) => <button type="button" className={NETWORK_BUTTON} key={item.id} disabled={blocked} onClick={() => setAttachments(current => current.filter(value => value.id !== item.id))}>{t('remove_attachment', { number: index+1 })}</button>)}</div></> : null}</> : null}
    {error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}{pending ? <p className="text-xs text-amber-950">{t('uncertain_submission')}</p> : null}
    <button className={NETWORK_PRIMARY} type="submit" disabled={busy || uploading || (!pending && !body.trim())}>{t(busy ? 'saving' : pending ? 'retry_same' : 'save_entry')}</button>
  </form>
}
export function NetworkComments({ recordId, kind, canWrite, mediaIds }: { recordId: string; kind: 'update' | 'post'; canWrite: boolean; mediaIds: string[] }) {
  const { t } = useTranslation('property_network')
  const [open, setOpen] = useState(false), [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false), [writeError, setError] = useState('')
  const read = useNetworkRead(`comments:${kind}:${recordId}:${page}`, signal => (kind === 'update' ? getUpdateComments : getPostComments)(recordId, { page }, signal), open)
  const data = read.data, error = writeError || (read.error ? networkError(read.error) : '')
  const [photoContext, setPhotoContext] = useState<string | null>(null)
  const selectedPhoto = photoContext !== null && mediaIds.includes(photoContext) ? photoContext : null
  function choosePhoto(value: string) {
    if (value && !mediaIds.includes(value)) return
    if (allowNetworkNavigation()) setPhotoContext(value || null)
  }
  async function hide(id: string) {
    setBusy(true); setError('')
    try { await hideNetworkComment(id); read.refresh() }
    catch (cause) { setError(networkError(cause)) }
    finally { setBusy(false) }
  }
  return <section className="mt-4 border-t border-line pt-3"><button type="button" aria-expanded={open} className={NETWORK_BUTTON} onClick={() => { if (!open || allowNetworkNavigation()) setOpen(value => !value) }}>{t(open ? 'hide_comments' : 'comments')}</button>{open ? <div className="mt-3 space-y-3">
    <NetworkFreshness read={read} />{busy ? <p role="status" className="text-sm text-dim">{t('loading')}</p> : null}{error ? <p role="alert" className="text-sm text-red-800">{t(error)}</p> : null}
    {data?.items.map(comment => <article key={comment.id} className="rounded-lg bg-line-soft p-3"><div className="flex flex-wrap justify-between gap-2"><p className="text-xs font-bold text-ink">{comment.author.display_name}</p><p className="text-xs text-dim"><NetworkDate value={comment.created_at} /></p></div><p className="mt-2 text-xs font-semibold text-sea">{comment.media_id === null ? t('comment_whole_entry') : mediaIds.includes(comment.media_id) ? t('comment_photo', { number: mediaIds.indexOf(comment.media_id)+1 }) : t('comment_photo_unavailable')}</p><p className="mt-2 whitespace-pre-wrap break-words text-sm text-ink2">{comment.body}</p>{comment.hidden ? <p className="mt-2 text-xs text-amber-950">{t('hidden')}</p> : null}{kind === 'post' && comment.can_hide && !comment.hidden ? <button type="button" className={NETWORK_BUTTON+' mt-2'} disabled={busy} onClick={() => void hide(comment.id)}>{t('hide_comment')}</button> : null}</article>)}
    {data && !data.items.length ? <p className="text-sm text-dim">{t('no_comments')}</p> : null}{data ? <NetworkPagination page={page} total={data.total} busy={busy} onChange={setPage} /> : null}
    {canWrite ? <><label className="mt-4 block text-sm font-semibold text-ink">{t('comment_context')}<select className={NETWORK_FIELD} value={selectedPhoto ?? ''} onChange={event => choosePhoto(event.target.value)}><option value="">{t('comment_whole_entry')}</option>{mediaIds.map((id, index) => <option key={id} value={id}>{t('comment_photo', { number: index+1 })}</option>)}</select></label>{selectedPhoto ? <PrivatePhotos ids={[selectedPhoto]} /> : null}<NetworkComposer key={`${kind}:${recordId}:${selectedPhoto ?? 'entry'}`} maxLength={2000} submit={input => {
      // Reject a stale context; never convert a photo-specific draft into a whole-entry comment.
      if (photoContext !== null && !mediaIds.includes(photoContext)) return Promise.reject(new Error('Photo context changed'))
      const body = { submission_id: input.submission_id, body: input.body, media_id: selectedPhoto }
      return kind === 'update' ? createUpdateComment(recordId, body) : createPostComment(recordId, body)
    }} onSaved={() => { setPage(1); read.refresh() }} /></> : <p className="text-xs text-dim">{t('comments_readonly')}</p>}
  </div> : null}</section>
}
