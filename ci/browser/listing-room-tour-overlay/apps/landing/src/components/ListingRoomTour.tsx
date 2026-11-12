"use client"

import { useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Images, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ListingRoomTour as RoomTour } from '../data/listingRoomTour'

type Selection = { groupIndex: number; photoIndex: number }

const button = 'inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-line-soft bg-white px-3 text-sm font-semibold text-ink hover:bg-paper focus-visible:outline-2 focus-visible:outline-coral aria-disabled:opacity-40'

export function ListingRoomTour({ tour }: { tour: RoomTour }) {
  const { t, i18n } = useTranslation('property_detail')
  const titleId = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const openButton = useRef<HTMLButtonElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const backButton = useRef<HTMLButtonElement>(null)
  const selectedThumbnail = useRef<HTMLButtonElement>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [opened, setOpened] = useState(false)
  const viewing = selection !== null
  const spanish = i18n.language.startsWith('es')
  const group = selection ? tour.groups[selection.groupIndex] : null
  const photo = selection && group ? group.photos[selection.photoIndex] : null
  const groupLabel = group ? (spanish ? group.label_es : group.label_en) : ''
  const caption = photo ? (spanish ? photo.caption_es : photo.caption_en) : undefined

  useEffect(() => {
    if (viewing) backButton.current?.focus()
    else selectedThumbnail.current?.focus()
  }, [viewing])

  useEffect(() => {
    if (!opened) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previousOverflow }
  }, [opened])

  function openTour() {
    const panel = dialog.current
    if (!panel || panel.open) return
    panel.showModal()
    setOpened(true)
    closeButton.current?.focus()
  }

  function finishClose() {
    selectedThumbnail.current = null
    setSelection(null)
    setOpened(false)
    openButton.current?.focus()
  }

  function movePhoto(offset: number) {
    setSelection(current => {
      if (!current) return current
      const lastIndex = tour.groups[current.groupIndex].photos.length - 1
      const photoIndex = Math.min(lastIndex, Math.max(0, current.photoIndex + offset))
      return photoIndex === current.photoIndex ? current : { ...current, photoIndex }
    })
  }

  return <>
    <button ref={openButton} type="button" className={`${button} mt-3`} onClick={openTour}>
      <Images size={18} aria-hidden="true" /> {t('room_tour.open')}
    </button>
    <dialog ref={dialog} aria-labelledby={titleId} onClose={finishClose}
      onKeyDown={event => {
        if (!viewing || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return
        event.preventDefault()
        movePhoto(event.key === 'ArrowLeft' ? -1 : 1)
      }}
      className="m-auto max-h-[92dvh] w-[calc(100%_-_1rem)] max-w-4xl overflow-y-auto rounded-2xl border-0 bg-white text-ink shadow-2xl backdrop:bg-black/70">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-line-soft bg-white p-3 sm:px-5">
        <h2 id={titleId} className="min-w-0 break-words text-lg font-bold">{t('room_tour.title')}</h2>
        <button ref={closeButton} type="button" className={button} aria-label={t('room_tour.close')} onClick={() => dialog.current?.close()}>
          <X size={20} aria-hidden="true" />
        </button>
      </div>
      {opened && <div hidden={viewing} className="space-y-5 p-3 sm:p-5">
        {tour.groups.map((room, groupIndex) => {
          const roomLabel = spanish ? room.label_es : room.label_en
          return <section key={room.id} aria-labelledby={`${titleId}-${room.id}`}>
            <h3 id={`${titleId}-${room.id}`} className="mb-2 break-words font-bold">{roomLabel}</h3>
            <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
              {room.photos.map((image, photoIndex) => <li key={image.url} className="min-w-0">
                <button type="button" className="block w-full overflow-hidden rounded-lg bg-paper focus-visible:outline-2 focus-visible:outline-coral"
                  aria-label={t('room_tour.photo', { room: roomLabel, number: photoIndex + 1, total: room.photos.length })}
                  onClick={event => {
                    selectedThumbnail.current = event.currentTarget
                    setSelection({ groupIndex, photoIndex })
                  }}>
                  <img src={image.url} alt="" loading="lazy" decoding="async" className="aspect-[4/3] w-full object-cover" />
                </button>
              </li>)}
            </ul>
          </section>
        })}
      </div>}
      {selection && group && photo && <div className="space-y-3 p-3 sm:p-5">
        <button ref={backButton} type="button" className={button} onClick={() => setSelection(null)}>
          <ArrowLeft size={18} aria-hidden="true" /> {t('room_tour.back')}
        </button>
        <h3 className="break-words font-bold">{groupLabel}</h3>
        <figure>
          <div className="grid min-h-32 place-items-center rounded-xl bg-black">
            <img src={photo.url} alt={caption ?? groupLabel} className="max-h-[60dvh] w-auto max-w-full object-contain" />
          </div>
          {caption && <figcaption className="mt-2 break-words text-sm text-ink2">{caption}</figcaption>}
        </figure>
        <div className="flex items-center justify-between gap-2">
          <button type="button" className={button} aria-label={t('room_tour.previous')} onClick={() => movePhoto(-1)} aria-disabled={selection.photoIndex === 0}>
            <ChevronLeft size={20} aria-hidden="true" />
          </button>
          <span aria-live="polite" className="text-center text-sm text-ink2">
            {t('room_tour.position', { number: selection.photoIndex + 1, total: group.photos.length })}
          </span>
          <button type="button" className={button} aria-label={t('room_tour.next')} onClick={() => movePhoto(1)} aria-disabled={selection.photoIndex === group.photos.length - 1}>
            <ChevronRight size={20} aria-hidden="true" />
          </button>
        </div>
      </div>}
    </dialog>
  </>
}
