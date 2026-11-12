export type ListingRoomPhoto = {
  url: string
  caption_en?: string
  caption_es?: string
}

export type ListingRoomGroup = {
  id: string
  label_en: string
  label_es: string
  photos: ListingRoomPhoto[]
}

export type ListingRoomTour = { groups: ListingRoomGroup[] }

export const ROOM_TOUR_MAX_GROUPS = 24
export const ROOM_TOUR_MAX_PHOTOS = 100
const GROUP_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const TEXT_CONTROLS = /[\u0000-\u001f\u007f]/
const SUPABASE_HOST = /^[a-z0-9][a-z0-9-]*\.supabase\.co$/
const BUCKET_PATH = '/storage/v1/object/public/listing-images/'
const LISTING_OBJECT_PATH = /^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp|heic)$/i
const NON_LISTING_PREFIXES = new Set(['avatars', 'documents', 'professionals', 'share', 'projects', 'private'])

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown, limit: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= limit
    && value.trim() === value && !TEXT_CONTROLS.test(value)
}

export function isRoomTourListingImage(value: string): boolean {
  try {
    const url = new URL(value)
    if (!url.pathname.startsWith(BUCKET_PATH)) return false
    const objectPath = url.pathname.slice(BUCKET_PATH.length)
    return url.href === value && url.protocol === 'https:' && SUPABASE_HOST.test(url.hostname)
      && !url.port && !url.username && !url.password && !url.search && !value.includes('#')
      && LISTING_OBJECT_PATH.test(objectPath) && !NON_LISTING_PREFIXES.has(objectPath.split('/', 1)[0].toLowerCase())
  } catch {
    return false
  }
}

/** A tour can select only photos already approved in this listing's public gallery. */
export function parseListingRoomTour(value: unknown, approvedImages: readonly string[]): ListingRoomTour | null {
  if (!Array.isArray(approvedImages) || !record(value) || Object.keys(value).length !== 1 || !Array.isArray(value.groups)
    || value.groups.length === 0 || value.groups.length > ROOM_TOUR_MAX_GROUPS) return null

  const approved = new Set(approvedImages)
  const photoUrls = new Set<string>()
  const groupIds = new Set<string>()
  const groups: ListingRoomGroup[] = []
  for (const group of value.groups) {
    if (!record(group) || Object.keys(group).length !== 4 || !text(group.id, 64) || !GROUP_ID.test(group.id)
      || groupIds.has(group.id) || !text(group.label_en, 80) || !text(group.label_es, 80)
      || !Array.isArray(group.photos) || group.photos.length === 0 || group.photos.length > ROOM_TOUR_MAX_PHOTOS - photoUrls.size) return null

    const photos: ListingRoomPhoto[] = []
    for (const photo of group.photos) {
      if (!record(photo) || typeof photo.url !== 'string' || !approved.has(photo.url)
        || photoUrls.has(photo.url) || !isRoomTourListingImage(photo.url)) return null
      const hasCaptions = Object.prototype.hasOwnProperty.call(photo, 'caption_en')
        || Object.prototype.hasOwnProperty.call(photo, 'caption_es')
      if (hasCaptions) {
        if (Object.keys(photo).length !== 3 || !text(photo.caption_en, 240) || !text(photo.caption_es, 240)) return null
        photos.push({ url: photo.url, caption_en: photo.caption_en, caption_es: photo.caption_es })
      } else {
        if (Object.keys(photo).length !== 1) return null
        photos.push({ url: photo.url })
      }
      photoUrls.add(photo.url)
    }
    groups.push({ id: group.id, label_en: group.label_en, label_es: group.label_es, photos })
    groupIds.add(group.id)
  }
  return { groups }
}

export function prepareRoomTourDraft(groups: ListingRoomGroup[], approvedImages: readonly string[]) {
  return parseListingRoomTour({ groups: groups.map(group => ({
    ...group,
    label_en: group.label_en.trim(), label_es: group.label_es.trim(),
    photos: group.photos.map(photo => {
      const captionEn = photo.caption_en?.trim() ?? '', captionEs = photo.caption_es?.trim() ?? ''
      return captionEn || captionEs ? { url: photo.url, caption_en: captionEn, caption_es: captionEs } : { url: photo.url }
    }),
  })) }, approvedImages)
}
