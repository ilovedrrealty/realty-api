import { useEffect, useId, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { useTranslation } from 'react-i18next'
import { parseListingRoomTour } from '../../../../../packages/listing-room-tour'
import type { AdminListingEdit } from '../../api/admin'
import { RoomPhotoTourPreview } from '../listings/RoomPhotoTourPreview'

const control = 'min-h-11 rounded-lg border border-line bg-white px-3 text-sm font-semibold text-ink disabled:opacity-40'

export function RoomPhotoTourEditReview({ edit, working, onApprove, onReject, onClose }: {
  edit: AdminListingEdit
  working: boolean
  onApprove: () => Promise<void>
  onReject: (reason: string) => Promise<void>
  onClose: () => void
}) {
  const { t } = useTranslation('common', { keyPrefix: 'room_photo_tour' })
  const id = useId()
  const dialog = useRef<HTMLDialogElement>(null), heading = useRef<HTMLHeadingElement>(null)
  const [previewState, setPreviewState] = useState<Record<string, boolean>>({}), [previewAttempt, setPreviewAttempt] = useState(0)
  const [reviewed, setReviewed] = useState(false), [reject, setReject] = useState(false), [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const images = edit.current_data.images
  const approved = Array.isArray(images) && images.every(url => typeof url === 'string') ? images as string[] : null
  const pending = edit.proposed_data.room_photo_tour
  const current = approved ? parseListingRoomTour(edit.current_data.room_photo_tour, approved) : null
  const proposed = approved ? parseListingRoomTour(pending, approved) : null
  const valid = !!approved && Object.keys(edit.proposed_data).length === 1 && (pending === null || !!proposed)
  const proposedPhotos = proposed?.groups.flatMap(group => group.photos.map(photo => photo.url)) ?? []
  const failed = proposedPhotos.some(url => previewState[url] === false)
  const photosReady = proposedPhotos.every(url => previewState[url] === true)

  useEffect(() => {
    const modal = dialog.current, trigger = document.activeElement
    modal?.showModal()
    heading.current?.focus()
    return () => {
      modal?.close()
      if (trigger instanceof HTMLElement) trigger.focus()
    }
  }, [])

  async function decide(approve: boolean) {
    if (working) return
    setError('')
    try {
      if (approve && valid && reviewed && photosReady) await onApprove()
      if (!approve && reason.trim()) await onReject(reason.trim())
    } catch (cause) {
      const stale = isAxiosError(cause) && [404, 409, 422].includes(cause.response?.status ?? 0)
      setError(stale ? 'review_stale' : 'review_unconfirmed')
      setReviewed(false)
    }
  }

  return <dialog ref={dialog} aria-labelledby={id} onCancel={event => { event.preventDefault(); if (!working) onClose() }}
    className="m-auto min-w-0 w-[calc(100%_-_1rem)] max-w-5xl max-h-[92dvh] overflow-hidden rounded-xl border-0 bg-paper text-ink backdrop:bg-black/60">
    <div className="flex max-h-[92dvh] min-w-0 flex-col">
    <header className="shrink-0 border-b border-line bg-white p-4">
      <h2 id={id} ref={heading} tabIndex={-1} className="text-lg font-bold text-ink">{t('admin_review')}</h2>
      <p className="break-words text-sm text-dim">{edit.listing_title}</p>
    </header>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
      <p className="text-sm text-dim">{t('admin_instructions')}</p>
      {!valid && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-900">{t('review_invalid')}</p>}
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <section aria-labelledby={`${id}-current`} className="min-w-0 rounded-xl border border-line bg-white p-3">
          <h3 id={`${id}-current`} className="mb-2 text-base font-bold">{t('current_tour')}</h3>
          <RoomPhotoTourPreview tour={current} />
        </section>
        <section aria-labelledby={`${id}-proposed`} className="min-w-0 rounded-xl border border-line bg-white p-3">
          <h3 id={`${id}-proposed`} className="mb-2 text-base font-bold">{t('proposed_tour')}</h3>
          {valid && <RoomPhotoTourPreview key={previewAttempt} tour={proposed} onImage={(url, loaded) =>
            setPreviewState(previous => previous[url] === loaded ? previous : { ...previous, [url]: loaded })} />}
          {valid && pending === null && <p className="mt-2 text-sm text-dim">{t('preview_removal')}</p>}
        </section>
      </div>
      {valid && !photosReady && <p role={failed ? 'alert' : 'status'} className="text-sm text-dim">{t(failed ? 'preview_failed' : 'preview_loading')}</p>}
      {failed && <button type="button" className={control} onClick={() => {
        setReviewed(false); setPreviewState({}); setPreviewAttempt(previous => previous + 1)
      }}>{t('retry_previews')}</button>}
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-900">{t(error)}</p>}
    </div>
    <div className="shrink-0 space-y-2 border-t border-line bg-white p-3">
      {reject ? <>
        <label className="block text-sm font-semibold">{t('reject_reason')}
          <input type="text" className="mt-1 block min-h-11 w-full rounded-lg border border-line px-3" value={reason}
            onChange={event => setReason(event.target.value)} disabled={working} />
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={control} disabled={working} onClick={() => setReject(false)}>{t('cancel')}</button>
          <button type="button" className={control} disabled={working || !reason.trim() || !!error} onClick={() => void decide(false)}>{t('confirm_reject')}</button>
        </div>
      </> : <>
        <label className="flex min-h-11 items-center gap-2 text-sm font-semibold">
          <input type="checkbox" checked={reviewed} disabled={!valid || !photosReady || working || !!error} onChange={event => setReviewed(event.target.checked)} />
          {t(pending === null ? 'removal_reviewed' : 'photos_reviewed')}
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={control} disabled={working} onClick={onClose}>{t('close_review')}</button>
          <button type="button" className={control} disabled={working || !!error} onClick={() => setReject(true)}>{t('reject')}</button>
          <button type="button" className={`${control} border-sea bg-sea text-white`} disabled={!valid || !photosReady || !reviewed || working || !!error}
            onClick={() => void decide(true)}>{t('approve')}</button>
        </div>
      </>}
    </div>
    </div>
  </dialog>
}
