import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { useTranslation } from 'react-i18next'
import {
  isRoomTourListingImage, prepareRoomTourDraft, ROOM_TOUR_MAX_GROUPS, ROOM_TOUR_MAX_PHOTOS,
  type ListingRoomGroup, type ListingRoomPhoto, type ListingRoomTour,
} from '../../../../../packages/listing-room-tour'
import { getRoomTourEditor, submitRoomTour, type RoomTourEditorData } from '../../api/listingRoomTour'
import { RoomPhotoTourPreview } from './RoomPhotoTourPreview'

const control = 'min-h-11 rounded-lg border border-line bg-white px-3 text-sm font-semibold text-ink disabled:opacity-40'
const field = 'mt-1 block min-h-11 w-full min-w-0 rounded-lg border border-line bg-white px-3 text-sm text-ink'

function move<T>(items: T[], index: number, offset: number) {
  const nextIndex = index + offset
  if (nextIndex < 0 || nextIndex >= items.length) return items
  const next = [...items]
  next[index] = items[nextIndex]
  next[nextIndex] = items[index]
  return next
}

export function RoomPhotoTourEditor({ listingId }: { listingId: string }) {
  const { t, i18n } = useTranslation('common', { keyPrefix: 'room_photo_tour' })
  const titleId = useId()
  const dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null)
  const reviewHeading = useRef<HTMLHeadingElement>(null), confirmation = useRef<HTMLParagraphElement>(null)
  const [opened, setOpened] = useState(false), [attempt, setAttempt] = useState(0)
  const [source, setSource] = useState<RoomTourEditorData | null>(null)
  const [groups, setGroups] = useState<ListingRoomGroup[]>([]), [activeId, setActiveId] = useState('')
  const [proposal, setProposal] = useState<ListingRoomTour | null | undefined>(undefined)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!opened) return
    const controller = new AbortController()
    void getRoomTourEditor(listingId, controller.signal).then(data => {
      if (controller.signal.aborted) return
      setSource(data)
      setGroups(data.room_photo_tour?.groups ?? [])
      setActiveId(data.room_photo_tour?.groups[0]?.id ?? '')
    }).catch(cause => {
      if (!controller.signal.aborted) setError(isAxiosError(cause) && [401, 403, 404].includes(cause.response?.status ?? 0)
        ? 'unavailable' : 'read_failed')
    })
    return () => controller.abort()
  }, [listingId, opened, attempt])

  useEffect(() => {
    if (saved) confirmation.current?.focus()
    else if (proposal !== undefined) reviewHeading.current?.focus()
  }, [proposal, saved])

  const images = useMemo(() => source ? [...new Set(source.images)].filter(isRoomTourListingImage) : [], [source])
  const prepared = useMemo(() => source ? prepareRoomTourDraft(groups, source.images) : null, [groups, source])
  const assigned = new Set(groups.flatMap(group => group.photos.map(photo => photo.url)))
  const active = groups.find(group => group.id === activeId)
  const activeIndex = active ? groups.indexOf(active) : -1
  const spanish = i18n.language.startsWith('es')

  function open() {
    if (!dialog.current || dialog.current.open) return
    setSource(null); setGroups([]); setActiveId(''); setProposal(undefined); setError(''); setSaved(false)
    dialog.current.showModal()
    setOpened(true)
  }

  function updateGroup(id: string, update: (group: ListingRoomGroup) => ListingRoomGroup) {
    setGroups(previous => previous.map(group => group.id === id ? update(group) : group))
  }

  function addGroup() {
    const id = `room-${crypto.randomUUID()}`
    setGroups(previous => [...previous, { id, label_en: '', label_es: '', photos: [] }])
    setActiveId(id)
  }

  function removeGroup(id: string) {
    const remaining = groups.filter(group => group.id !== id)
    setGroups(remaining)
    setActiveId(remaining[Math.min(activeIndex, remaining.length - 1)]?.id ?? '')
  }

  function updatePhoto(group: ListingRoomGroup, url: string, changes: Partial<ListingRoomPhoto>) {
    updateGroup(group.id, row => ({ ...row, photos: row.photos.map(photo => photo.url === url ? { ...photo, ...changes } : photo) }))
  }

  async function submit() {
    if (!source || proposal === undefined || busy || saved) return
    setBusy(true); setError('')
    try {
      await submitRoomTour(listingId, proposal, source.images)
      setSaved(true)
    } catch (cause) {
      const status = isAxiosError(cause) ? cause.response?.status : undefined
      setError(status === 409 ? 'conflict' : status === 422 ? 'invalid' : 'submit_unconfirmed')
    } finally { setBusy(false) }
  }

  return <div className="mb-5">
    <button ref={trigger} type="button" className={control} onClick={open}>{t('edit')}</button>
    <dialog ref={dialog} aria-labelledby={titleId} onCancel={event => { if (busy) event.preventDefault() }}
      onClose={() => { setOpened(false); trigger.current?.focus() }}
      className="m-auto max-h-[90dvh] w-[calc(100%_-_1rem)] max-w-4xl overflow-y-auto rounded-2xl border-0 bg-white text-ink shadow-2xl backdrop:bg-black/60">
      <header className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 border-b border-line bg-white p-4">
        <h2 id={titleId} className="min-w-0 break-words text-lg font-bold">{t('title')}</h2>
        <button type="button" className={control} disabled={busy} onClick={() => dialog.current?.close()}>{t('close')}</button>
      </header>
      {opened && <div className="space-y-4 p-4 sm:p-5">
        {saved ? <p ref={confirmation} tabIndex={-1} role="status" className="rounded-lg bg-paper p-4 font-semibold">{t('submitted')}</p> : <>
          {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900">{t(error)}</p>}
          {!source && !error && <p role="status">{t('loading')}</p>}
          {!source && error === 'read_failed' && <button type="button" className={control} onClick={() => {
            setError(''); setAttempt(previous => previous + 1)
          }}>{t('retry_read')}</button>}
          {source && source.pending_review === 'other' && <p role="status" className="rounded-lg bg-paper p-3 text-sm">{t('other_pending')}</p>}
          {source && source.pending_review !== 'other' && (proposal === undefined ? <>
            <p className="text-sm text-dim">{t('instructions')}</p>
            {!images.length && <p role="status">{t('no_approved_photos')}</p>}
            {source.pending_review === 'room_photo_tour' && <p role="status" className="rounded-lg bg-paper p-3 text-sm">{t('room_pending')}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="button" className={control} disabled={!images.length || groups.length >= ROOM_TOUR_MAX_GROUPS} onClick={addGroup}>{t('add_room')}</button>
              {source.room_photo_tour && <button type="button" className={control} onClick={() => { setError(''); setProposal(null) }}>{t('remove_tour')}</button>}
            </div>
            {!!groups.length && <ol className="space-y-2">
              {groups.map((group, index) => <li key={group.id} className="flex flex-wrap gap-2 rounded-lg bg-paper p-2">
                <button type="button" className={`${control} min-w-0 flex-1 break-words text-left`} aria-pressed={group.id === activeId}
                  onClick={() => setActiveId(group.id)}>{(spanish ? group.label_es : group.label_en) || t('room_number', { number: index + 1 })} · {group.photos.length}</button>
                <button type="button" className={control} disabled={index === 0} onClick={() => setGroups(previous => move(previous, index, -1))}>{t('move_up')}</button>
                <button type="button" className={control} disabled={index === groups.length - 1} onClick={() => setGroups(previous => move(previous, index, 1))}>{t('move_down')}</button>
              </li>)}
            </ol>}
            {active && <section className="space-y-4 rounded-xl border border-line p-3 sm:p-4">
              <h3 className="font-bold">{t('edit_room', { number: activeIndex + 1 })}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-sm font-semibold">{t('label_en')}<input lang="en" className={field} maxLength={80} value={active.label_en}
                  onChange={event => updateGroup(active.id, group => ({ ...group, label_en: event.target.value }))} /></label>
                <label className="text-sm font-semibold">{t('label_es')}<input lang="es" className={field} maxLength={80} value={active.label_es}
                  onChange={event => updateGroup(active.id, group => ({ ...group, label_es: event.target.value }))} /></label>
              </div>
              <button type="button" className={control} onClick={() => removeGroup(active.id)}>{t('remove_room')}</button>
              <h4 className="font-semibold">{t('selected_photos')}</h4>
              {!active.photos.length && <p className="text-sm text-dim">{t('no_selected_photos')}</p>}
              {!!active.photos.length && <ol className="space-y-3">
                {active.photos.map((photo, index) => <li key={photo.url} className="grid gap-3 rounded-xl border border-line p-3 sm:grid-cols-[8rem_1fr]">
                  <img src={photo.url} loading="lazy" decoding="async" referrerPolicy="no-referrer" alt={t('preview_photo', { number: index + 1 })}
                    className="aspect-[4/3] w-full rounded-lg bg-paper object-contain" />
                  <div className="min-w-0 space-y-2">
                    <label className="block text-sm">{t('caption_en')}<input lang="en" className={field} maxLength={240} value={photo.caption_en ?? ''}
                      onChange={event => updatePhoto(active, photo.url, { caption_en: event.target.value })} /></label>
                    <label className="block text-sm">{t('caption_es')}<input lang="es" className={field} maxLength={240} value={photo.caption_es ?? ''}
                      onChange={event => updatePhoto(active, photo.url, { caption_es: event.target.value })} /></label>
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className={control} disabled={index === 0} onClick={() => updateGroup(active.id, group => ({ ...group, photos: move(group.photos, index, -1) }))}>{t('move_up')}</button>
                      <button type="button" className={control} disabled={index === active.photos.length - 1} onClick={() => updateGroup(active.id, group => ({ ...group, photos: move(group.photos, index, 1) }))}>{t('move_down')}</button>
                      <button type="button" className={control} onClick={() => updateGroup(active.id, group => ({ ...group, photos: group.photos.filter(item => item.url !== photo.url) }))}>{t('remove_photo')}</button>
                    </div>
                  </div>
                </li>)}
              </ol>}
              <h4 className="font-semibold">{t('approved_gallery')}</h4>
              <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {images.map((url, index) => <li key={url} className="min-w-0">
                  <button type="button" className={`${control} w-full min-w-0 p-2`} disabled={assigned.has(url) || assigned.size >= ROOM_TOUR_MAX_PHOTOS}
                    aria-label={t('choose_photo', { number: index + 1 })}
                    onClick={() => updateGroup(active.id, group => ({ ...group, photos: [...group.photos, { url }] }))}>
                    <img src={url} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" className="aspect-[4/3] w-full rounded bg-paper object-contain" />
                    <span className="mt-1 block break-words">{t(assigned.has(url) ? 'photo_assigned' : 'add_photo')}</span>
                  </button>
                </li>)}
              </ul>
            </section>}
            <p className="text-sm text-dim">{t('requirements')}</p>
            <button type="button" className={control} disabled={!prepared || JSON.stringify(prepared) === JSON.stringify(source.room_photo_tour)}
              onClick={() => { if (prepared) { setError(''); setProposal(prepared) } }}>{t('preview')}</button>
          </> : <>
            <h3 ref={reviewHeading} tabIndex={-1} className="font-bold">{t('preview')}</h3>
            <p className="text-sm text-dim">{t(proposal ? 'preview_help' : 'preview_removal')}</p>
            <RoomPhotoTourPreview tour={proposal} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className={control} disabled={busy} onClick={() => { setError(''); setProposal(undefined) }}>{t('back_to_draft')}</button>
              <button type="button" className={control} disabled={busy || !!error} onClick={() => void submit()}>
                {t(busy ? 'submitting' : source.pending_review === 'room_photo_tour' ? 'replace_proposal' : 'submit')}
              </button>
            </div>
          </>)}
        </>}
      </div>}
    </dialog>
  </div>
}
