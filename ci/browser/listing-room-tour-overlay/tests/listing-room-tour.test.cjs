'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { resolve } = require('node:path')
const { pathToFileURL } = require('node:url')
const { readFileSync } = require('node:fs')

const src = resolve(__dirname, '../apps/landing/src')
const contractUrl = pathToFileURL(resolve(__dirname, '../packages/listing-room-tour/index.ts')).href
const objectBase = 'https://synthetic-project.supabase.co/storage/v1/object/public/listing-images/synthetic-listing/'
const cover = objectBase + 'cover.jpg'
const kitchen = objectBase + 'kitchen.jpg'
const detail = objectBase + 'kitchen-detail.webp'
const bedroom = objectBase + 'bedroom.png'
const images = Object.freeze([cover, bedroom, kitchen, detail])
const tour = {
  groups: [
    { id: 'kitchen', label_en: 'Kitchen', label_es: 'Cocina', photos: [
      { url: kitchen, caption_en: 'Kitchen counter', caption_es: 'Encimera de la cocina' },
      { url: detail },
    ] },
    { id: 'bedroom', label_en: 'Bedroom', label_es: 'Dormitorio', photos: [{ url: bedroom }] },
  ],
}

async function parseTour(value, approved = images) {
  const { parseListingRoomTour } = await import(contractUrl)
  return parseListingRoomTour(value, approved)
}

test('preserves explicit room and photo order without altering the full gallery or cover', async () => {
  const parsed = await parseTour(tour)
  assert.deepEqual(parsed, tour)
  assert.notEqual(parsed.groups, tour.groups)
  assert.deepEqual(images, [cover, bedroom, kitchen, detail])
  assert.equal(parsed.groups[0].photos[0].caption_es, 'Encimera de la cocina')
})

test('missing, empty, malformed, or non-bilingual optional data leaves the legacy gallery available', async () => {
  for (const value of [undefined, null, { groups: [] }, {}, { groups: tour.groups, draft: true },
    { groups: [{ ...tour.groups[0], label_es: '' }] }, { groups: [{ ...tour.groups[0], label_es: ' Cocina' }] },
    { groups: [{ ...tour.groups[0], photos: [] }] },
    { groups: [{ ...tour.groups[0], photos: [{ url: kitchen, caption_en: 'Single language' }] }] },
    { groups: [{ ...tour.groups[0], photos: [{ url: kitchen, object_key: 'do not expose' }] }] },
  ]) assert.equal(await parseTour(value), null)
  assert.equal(await parseTour(tour, { images }), null)
})

test('can group an already-published gallery with 36 photos without changing the upload limit', async () => {
  const existing = Array.from({ length: 36 }, (_, index) => objectBase + `photo-${index}.jpg`)
  const groups = Array.from({ length: 9 }, (_, index) => ({
    id: `room-${index + 1}`,
    label_en: `Room ${index + 1}`,
    label_es: `Ambiente ${index + 1}`,
    photos: existing.slice(index * 4, (index + 1) * 4).map(url => ({ url })),
  }))
  assert.deepEqual(await parseTour({ groups }, existing), { groups })
})

test('rejects a stale photo reference or a duplicate photo or group across the entire tour', async () => {
  assert.equal(await parseTour(tour, [cover, bedroom, kitchen]), null)
  const [first, second] = tour.groups
  assert.equal(await parseTour({ groups: [first, { ...second, photos: [{ url: kitchen }] }] }), null)
  assert.equal(await parseTour({ groups: [first, { ...second, id: first.id }] }), null)
})

test('refuses private, signed, foreign, credentialed and noncanonical references even if in the images input', async () => {
  for (const url of [
    kitchen.replace('/object/public/listing-images/', '/object/authenticated/property-network-private/'),
    kitchen.replace('/object/public/listing-images/', '/object/sign/property-network-private/') + '?token=synthetic',
    kitchen.replace('/listing-images/', '/property-network-private/'),
    objectBase.replace('/synthetic-listing/', '/professionals/') + 'photo.jpg',
    objectBase.replace('/synthetic-listing/', '/share/') + 'photo.jpg',
    kitchen.replace('synthetic-project.supabase.co', 'external.example'),
    kitchen.replace('https://', 'https://synthetic-user@'),
    kitchen.replace('/synthetic-listing/', '/synthetic-listing/../'),
    kitchen.replace('kitchen.jpg', 'private%2fphoto.jpg'),
    kitchen + '?token=synthetic',
    kitchen + '#',
    kitchen + '#photo',
    '/property-network/media/synthetic',
  ]) {
    const payload = { groups: [{ ...tour.groups[0], photos: [{ url }] }] }
    assert.equal(await parseTour(payload, [url]), null)
  }
})

test('preserves exact Supabase public gallery URLs with an empty query delimiter', async () => {
  const exactImage = kitchen + '?'
  const input = { groups: [{ ...tour.groups[0], photos: [{ url: exactImage }] }] }
  assert.deepEqual(await parseTour(input, [exactImage]), input)
  assert.equal(await parseTour(input, [kitchen]), null)
})

test('an author draft keeps explicit ordering and requires both captions or no caption', async () => {
  const { prepareRoomTourDraft } = await import(contractUrl)
  const captioned = { url: kitchen, caption_en: '  Kitchen counter ', caption_es: ' Encimera de la cocina  ' }
  const group = { id: 'kitchen', label_en: ' Kitchen ', label_es: ' Cocina ', photos: [captioned, { url: detail, caption_en: ' ', caption_es: '' }] }
  assert.deepEqual(prepareRoomTourDraft([group], images), { groups: [tour.groups[0]] })
  assert.equal(prepareRoomTourDraft([{ ...group, photos: [{ ...captioned, caption_es: '' }] }], images), null)
  assert.equal(prepareRoomTourDraft([group], [cover, detail]), null)
  assert.equal(prepareRoomTourDraft([], images), null)
})

test('authoring and Admin decisions have independent English and Spanish controls', () => {
  const dashboard = resolve(src, '../../dashboard/src')
  const strings = lang => JSON.parse(readFileSync(resolve(dashboard, `locale/${lang}/common.json`), 'utf8')).room_photo_tour
  const english = strings('en'), spanish = strings('es')
  assert.deepEqual(Object.keys(english), Object.keys(spanish))
  for (const key of ['edit', 'submit', 'replace_proposal', 'admin_review', 'photos_reviewed', 'approve', 'confirm_reject', 'preview_removal']) {
    assert.ok(english[key] && spanish[key])
    assert.notEqual(english[key], spanish[key])
  }
})

test('room proposals require visual review in both Admin queue layouts before approval', () => {
  const dashboard = resolve(src, '../../dashboard/src')
  const queue = readFileSync(resolve(dashboard, 'pages/admin/Listings.tsx'), 'utf8')
  const pending = queue.split('Pending Edits section')[1].split('Active Deals section')[0]
  const [room, ordinary] = pending.split(') : <>')
  assert.match(room, /\{isRoomTourEdit\(edit\) \? \(/)
  assert.match(room, /aria-label=\{tRoom\('admin_review'\)\}/)
  assert.doesNotMatch(room, /handleApproveEdit/)
  assert.equal((ordinary.match(/handleApproveEdit\(edit\)/g) ?? []).length, 2)
  assert.equal((queue.match(/handleApproveEdit\(selectedEdit, true\)/g) ?? []).length, 1)
  assert.match(queue, /if \(roomTourReviewed !== isRoomTourEdit\(edit\)\) throw/)
  const review = readFileSync(resolve(dashboard, 'components/admin/RoomPhotoTourEditReview.tsx'), 'utf8')
  assert.match(review, /if \(approve && valid && reviewed && photosReady\) await onApprove\(\)/)
  const api = readFileSync(resolve(dashboard, 'api/admin.ts'), 'utf8')
  assert.match(api, /approveListingEdit\(id: string\)[\s\S]*?client\.post\(`\/admin\/listing-edits\/\$\{id\}\/approve`\)/)
  assert.match(api, /approveListingRoomPhotoTour\(id: string\)[\s\S]*?client\.post\(`\/admin\/listing-edits\/\$\{id\}\/approve`, \{ room_photo_tour_reviewed: true \}\)/)
})

test('room viewer controls have independent English and Spanish copy and numbered placeholders', () => {
  const roomTour = lang => JSON.parse(readFileSync(resolve(src, `locales/${lang}/property_detail.json`), 'utf8')).room_tour
  const english = roomTour('en'), spanish = roomTour('es')
  assert.deepEqual(Object.keys(english), Object.keys(spanish))
  for (const key of Object.keys(english)) {
    assert.ok(english[key] && spanish[key])
    assert.notEqual(english[key], spanish[key])
    const placeholders = value => (value.match(/{{\w+}}/g) ?? []).sort()
    assert.deepEqual(placeholders(english[key]), placeholders(spanish[key]))
  }
})

test('the room tour follows the visible photo gallery if a selected 3D tab has no safe tour URL', () => {
  const source = readFileSync(resolve(src, 'views/PropertyDetail.tsx'), 'utf8')
  assert.match(source, /const safeTourUrl = listing\.tour_3d_url \? safeUrl\(listing\.tour_3d_url\) : null/)
  assert.match(source, /const activeTourUrl = galleryTab === '3dtour' \? safeTourUrl : null/)
  const gallery = source.split('{/* Gallery */}')[1].split('{/* Main two-column layout */}')[0]
  assert.match(gallery, /\{activeTourUrl \? \([\s\S]+<iframe\s+src=\{activeTourUrl\}/)
  assert.match(gallery, /\{!activeTourUrl && roomTour && <ListingRoomTour\b/)
})
