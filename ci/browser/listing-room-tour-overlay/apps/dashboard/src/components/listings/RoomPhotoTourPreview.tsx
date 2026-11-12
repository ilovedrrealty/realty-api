import { useTranslation } from 'react-i18next'
import type { ListingRoomTour } from '../../../../../packages/listing-room-tour'

export function RoomPhotoTourPreview({ tour, onImage }: {
  tour: ListingRoomTour | null
  onImage?: (url: string, loaded: boolean) => void
}) {
  const { t } = useTranslation('common', { keyPrefix: 'room_photo_tour' })
  if (!tour) return <p className="text-sm text-dim">{t('no_tour')}</p>
  return <ol className="min-w-0 space-y-5">
    {tour.groups.map(group => <li key={group.id} className="min-w-0 rounded-xl border border-line p-3">
      <h4 className="break-words text-base font-bold text-ink">
        <span lang="en">{group.label_en}</span> / <span lang="es">{group.label_es}</span>
      </h4>
      <ol className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
        {group.photos.map((photo, index) => <li key={photo.url} className="min-w-0">
          <figure>
            <img src={photo.url} alt={t('preview_photo', { number: index + 1 })} loading="lazy" decoding="async"
              referrerPolicy="no-referrer" className="max-h-[50vh] w-full rounded-lg bg-paper object-contain"
              onLoad={event => onImage?.(photo.url, event.currentTarget.naturalWidth > 0)}
              onError={() => onImage?.(photo.url, false)} />
            {(photo.caption_en || photo.caption_es) && <figcaption className="mt-1 break-words text-sm text-dim">
              <p lang="en">{photo.caption_en}</p><p lang="es">{photo.caption_es}</p>
            </figcaption>}
          </figure>
        </li>)}
      </ol>
    </li>)}
  </ol>
}
