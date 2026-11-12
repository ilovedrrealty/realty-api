import client from './axios'
import { parseListingRoomTour, type ListingRoomTour } from '../../../../packages/listing-room-tour'

export type RoomTourEditorData = {
  images: string[]
  room_photo_tour: ListingRoomTour | null
  pending_review: 'none' | 'room_photo_tour' | 'other'
}

const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/

function roomTourPath(listingId: string) {
  if (!uuid.test(listingId)) throw new Error('Invalid listing ID')
  return `/listings/${listingId}/room-photo-tour`
}

export async function getRoomTourEditor(listingId: string, signal: AbortSignal): Promise<RoomTourEditorData> {
  const { data, status, headers } = await client.get(roomTourPath(listingId), { signal, timeout: 15000 })
  if (status !== 200 || !String(headers['cache-control']).split(',').some(value => value.trim().toLowerCase() === 'no-store')
    || !data || typeof data !== 'object' || Object.keys(data).length !== 4 || data.listing_id !== listingId
    || !Array.isArray(data.images) || data.images.some((url: unknown) => typeof url !== 'string')
    || !['none', 'room_photo_tour', 'other'].includes(data.pending_review)) throw new Error('Invalid room tour editor response')
  const tour = data.room_photo_tour === null ? null : parseListingRoomTour(data.room_photo_tour, data.images)
  if (data.room_photo_tour !== null && !tour) throw new Error('Invalid published room tour')
  return { images: data.images, room_photo_tour: tour, pending_review: data.pending_review }
}

export async function submitRoomTour(listingId: string, tour: ListingRoomTour | null, approvedImages: string[]) {
  const payload = tour === null ? null : parseListingRoomTour(tour, approvedImages)
  if (tour && !payload) throw new Error('Invalid room tour proposal')
  const { data, status } = await client.put(roomTourPath(listingId), { room_photo_tour: payload }, { timeout: 15000 })
  if (status !== 202 || !data || typeof data !== 'object' || Object.keys(data).length !== 2
    || data.listing_id !== listingId || typeof data.message !== 'string') throw new Error('Unconfirmed room tour proposal')
}
