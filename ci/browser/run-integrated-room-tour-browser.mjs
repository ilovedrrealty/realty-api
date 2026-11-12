import { lstat, readFile } from 'node:fs/promises'
import { isDeepStrictEqual } from 'node:util'
import { deflateSync } from 'node:zlib'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const landing = 'http://127.0.0.1:8917'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const photoOrigin = 'https://synthetic-room-tour.supabase.co'
const photoBase = `${photoOrigin}/storage/v1/object/public/listing-images/fixture-owner/`
const reviewPath = '/admin/listing-edits'
const roles = { owner: 'owner', admin: 'admin', outsider: 'buyer' }
const wait = 15_000
const images = [
  { url: `${photoBase}exterior-cover.jpg?`, width: 480, height: 270, color: [69, 112, 123] },
  { url: `${photoBase}living-room.jpg`, width: 360, height: 240, color: [202, 131, 74] },
  { url: `${photoBase}primary-bedroom.jpg`, width: 200, height: 360, color: [105, 78, 147] },
]
const rooms = [
  { en: 'Synthetic living room', es: 'Sala de prueba', captionEn: 'Wide living room photo', captionEs: 'Foto amplia de la sala' },
  { en: 'Synthetic primary bedroom', es: 'Dormitorio principal de prueba',
    captionEn: 'Portrait view of the bedroom', captionEs: 'Vista vertical del dormitorio' },
]
const copy = {
  en: { open: 'Browse photos by room', title: 'Room-by-room photo tour', close: 'Close room tour', back: 'Back to rooms',
    previous: 'Previous photo', next: 'Next photo', photo: room => `${room}, photo 1 of 1`, booking: 'Request to book',
    galleryTile: (index, total) => `View photo ${index + 1} of ${total}` },
  es: { open: 'Ver fotos por ambiente', title: 'Recorrido fotográfico por ambientes', close: 'Cerrar recorrido fotográfico',
    back: 'Volver a los ambientes', previous: 'Foto anterior', next: 'Foto siguiente', photo: room => `${room}, foto 1 de 1`,
    booking: 'Solicitar reserva', galleryTile: (index, total) => `Ver foto ${index + 1} de ${total}` },
}
const editorCopy = { open: 'Editar recorrido fotográfico', editListing: 'Editar propiedad', addRoom: 'Agregar ambiente',
  labelEn: 'Nombre del ambiente en inglés', labelEs: 'Nombre del ambiente en español',
  captionEn: 'Descripción de la foto en inglés (opcional)', captionEs: 'Descripción de la foto en español (opcional)',
  choose: index => `Agregar foto aprobada ${index}`, preview: 'Revisar la propuesta de recorrido',
  submit: 'Enviar a revisión de administración', saved: 'Recorrido fotográfico enviado a revisión de administración.' }

const result = { status: 'FAIL', scope: 'isolated_synthetic_listing_room_tour', checks: [], stage_writes: 0, approval_writes: 0,
  anonymous_phases_verified: 0, synthetic_images_verified: 0, public_languages_verified: 0, short_gallery_tiles_verified: 0,
  blocked_listing_view_posts: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = []
const fulfilled = new Set()
let stage = 'fixture'
let editId = null
let blockedMapFrames = 0
let unexpectedExternalNavigation = 0

class RehearsalFailure extends Error {}
function require(condition, code) {
  if (!condition) throw new RehearsalFailure(code)
}
function keys(value, fields) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field))
}
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const listingPath = f => `/listings/${f.listingId}`
const editorPath = f => `${listingPath(f)}/room-photo-tour`
const approvalPath = id => `${reviewPath}/${id}/approve`
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const dialog = (scope, name) => scope.getByRole('dialog', { name, exact: true })
const noStore = response => response.headers()['cache-control'] === 'no-store'

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(keys(fixture, ['accounts', 'roomTour']) && keys(fixture.accounts, Object.keys(roles)), 'EXACT_SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(fixture.accounts)) {
    require(keys(account, ['email', 'password']) && account.email === `browser-room-tour-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const f = fixture.roomTour
  require(keys(f, ['listingId', 'title', 'images', 'photoOrigin']) && uuid.test(f.listingId)
    && f.title === 'Synthetic villa for room photo tour' && f.photoOrigin === photoOrigin
    && isDeepStrictEqual(f.images, images.map(image => image.url)), 'EXACT_SYNTHETIC_GALLERY_REQUIRED')
  return fixture
}

function pngChunk(type, body) {
  const content = Buffer.concat([Buffer.from(type, 'ascii'), body])
  let crc = 0xffffffff
  for (const value of content) {
    crc ^= value
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1))
  }
  const size = Buffer.alloc(4), checksum = Buffer.alloc(4)
  size.writeUInt32BE(body.length)
  checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0)
  return Buffer.concat([size, content, checksum])
}

function syntheticPng({ width, height, color }) {
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8
  header[9] = 2
  const stride = 1 + width * 3
  const pixels = Buffer.alloc(stride * height)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = y * stride + 1 + x * 3
      const edge = y < 6 || y >= height - 6 || x < 6 || x >= width - 6
      for (let channel = 0; channel < 3; channel += 1) pixels[offset + channel] = edge ? 246 : color[channel]
    }
  }
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), pngChunk('IHDR', header),
    pngChunk('IDAT', deflateSync(pixels)), pngChunk('IEND', Buffer.alloc(0))])
}

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function session(browser, fixture, role, lang, synthetic) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    locale: lang === 'es' ? 'es-DO' : 'en-US', timezoneId: lang === 'es' ? 'Asia/Tokyo' : 'America/Los_Angeles' })
  contexts.push(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, landing, lang }) => {
    if (location.origin !== dashboard && location.origin !== landing) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, landing, lang })

  const f = fixture.roomTour
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method()
    if (url.origin === photoOrigin && method === 'GET' && request.resourceType() === 'image') {
      const source = url.href === images[0].url.slice(0, -1) ? images[0].url : url.href
      const image = synthetic.get(source)
      if (image) {
        fulfilled.add(source)
        result.synthetic_images_verified = fulfilled.size
        return route.fulfill({ status: 200, contentType: 'image/png', headers: { 'cache-control': 'no-store' }, body: image })
      }
    }
    if (![dashboard, landing, api].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) {
        let iframe = request.headers()['sec-fetch-dest'] === 'iframe'
        try { iframe ||= !!request.frame().parentFrame() } catch { /* Unbound requests still fail the navigation guard. */ }
        const fixtureMap = role === 'guest' && iframe && method === 'GET' && url.origin === 'https://maps.google.com'
          && url.pathname === '/maps' && url.searchParams.get('output') === 'embed'
          && url.searchParams.get('q') === 'Synthetic listing area, Dominican Republic'
        if (fixtureMap) blockedMapFrames += 1
        else unexpectedExternalNavigation += 1
      }
      return route.abort('blockedbyclient')
    }
    if (method === 'POST' && url.origin === api && url.pathname === `${listingPath(f)}/view` && role === 'guest') {
      result.blocked_listing_view_posts += 1
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      if (url.origin === api && role === 'owner' && method === 'PUT' && url.pathname === editorPath(f) && !result.stage_writes) {
        result.stage_writes += 1
      } else if (url.origin === api && role === 'admin' && method === 'POST' && editId
        && url.pathname === approvalPath(editId) && !result.approval_writes) {
        result.approval_writes += 1
      } else {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
    }
    return route.continue()
  })
  const account = fixture.accounts[role]
  if (account) {
    const login = await context.request.post(`${api}/auth/login`, { data: account, headers: { Origin: dashboard },
      maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
      require(login.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'REAL_SYNTHETIC_AUTH_REQUIRED')
    } finally { await login.dispose() }
  }
  return { context, page: await context.newPage(), role, account, lang }
}

async function dashboardListings(state) {
  const { page, role, account } = state
  await page.bringToFront()
  const path = role === 'admin' ? '/listings?workspace=admin' : '/listings'
  const [auth] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(dashboard + path, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await auth.json()
  require(auth.status() === 200 && user.role === roles[role] && user.email === account.email, 'SIGNED_IN_ROLE_MISMATCH')
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

async function fitMobile(locator) {
  await locator.scrollIntoViewIfNeeded()
  require(await locator.evaluate(element => {
    const box = element.getBoundingClientRect()
    return innerWidth === 390 && innerHeight === 844 && box.left >= -1 && box.right <= innerWidth + 1
      && box.top >= -1 && box.bottom <= innerHeight + 1
  }), 'MOBILE_VIEWPORT_OVERFLOW')
}

function exactDetail(value, f, tour) {
  require(value.id === f.listingId && value.title === f.title && value.transaction === 'rent'
    && isDeepStrictEqual(value.images, f.images) && isDeepStrictEqual(value.room_photo_tour, tour), 'EXACT_PUBLIC_LISTING_DETAIL_REQUIRED')
}

async function directPublic(guest, f, tour) {
  require(!(await guest.context.cookies(api)).some(cookie => cookie.name === 'ildr_token'), 'ANONYMOUS_PUBLIC_SESSION_REQUIRED')
  const response = await guest.context.request.get(api + listingPath(f), { maxRedirects: 0, timeout: wait })
  try {
    require(response.status() === 200 && noStore(response), 'PUBLIC_DETAIL_STATUS_OR_CACHE')
    exactDetail(await response.json(), f, tour)
    result.anonymous_phases_verified += 1
  } finally { await response.dispose() }
}

async function openPublic(guest, f, tour) {
  const page = guest.page
  await page.bringToFront()
  const [detail, document] = await Promise.all([
    page.waitForResponse(response => endpoint(response, listingPath(f))),
    page.goto(`${landing}/listing/${f.listingId}/`, { waitUntil: 'domcontentloaded' }),
  ])
  require(document?.status() === 200 && detail.status() === 200 && noStore(detail), 'REAL_EXPORTED_LISTING_REQUIRED')
  exactDetail(await detail.json(), f, tour)
  const visibleTitle = f.title.toLowerCase().replace(/\b\w/g, character => character.toUpperCase())
  await page.getByRole('heading', { name: visibleTitle, exact: true, level: 1 }).waitFor()
  await button(page, copy[guest.lang].booking).waitFor()
  if (tour === null) require(await button(page, copy[guest.lang].open).count() === 0, 'UNAPPROVED_TOUR_VISIBLE_PUBLICLY')
}

async function flatCover({ page, lang }, f) {
  const cover = button(page, copy[lang].galleryTile(0, f.images.length))
  await cover.waitFor()
  require(await cover.evaluate((element, url) => element.style.backgroundImage.includes(url), f.images[0]), 'FLAT_GALLERY_COVER_CHANGED')
  await cover.click()
  const original = page.locator(`img[src="${f.images[0]}"]`).filter({ visible: true })
  await original.waitFor()
  await page.waitForFunction(img => img.complete && img.naturalWidth === 480 && img.naturalHeight === 270,
    await original.elementHandle())
  await page.keyboard.press('Escape')
  await original.waitFor({ state: 'hidden' })
}

function exactProposal(tour, f) {
  require(keys(tour, ['groups']) && Array.isArray(tour.groups) && tour.groups.length === 2, 'EXACT_TWO_ROOM_PROPOSAL_REQUIRED')
  for (let index = 0; index < rooms.length; index += 1) {
    const room = rooms[index], group = tour.groups[index]
    require(keys(group, ['id', 'label_en', 'label_es', 'photos']) && group.id.startsWith('room-') && uuid.test(group.id.slice(5))
      && group.label_en === room.en && group.label_es === room.es && Array.isArray(group.photos) && group.photos.length === 1
      && isDeepStrictEqual(group.photos[0], { url: f.images[index + 1], caption_en: room.captionEn, caption_es: room.captionEs }),
    'EXACT_BILINGUAL_INTERIOR_METADATA_REQUIRED')
  }
  require(tour.groups[0].id !== tour.groups[1].id, 'ROOM_IDENTITIES_MUST_BE_UNIQUE')
  return tour
}

async function stageFromOwner(owner, f) {
  await dashboardListings(owner)
  const page = owner.page
  const listing = page.getByText(new RegExp(`^${f.title}$`, 'i')).filter({ visible: true })
  await listing.click()
  await button(page, editorCopy.editListing).click()
  const [approved] = await Promise.all([
    page.waitForResponse(response => endpoint(response, editorPath(f))),
    button(page, editorCopy.open).click(),
  ])
  const editor = await approved.json()
  require(approved.status() === 200 && noStore(approved) && keys(editor, ['listing_id', 'images', 'room_photo_tour', 'pending_review'])
    && editor.listing_id === f.listingId && isDeepStrictEqual(editor.images, f.images)
    && editor.room_photo_tour === null && editor.pending_review === 'none', 'EXACT_AUTHORIZED_EDITOR_REQUIRED')
  const modal = dialog(page, copy.es.title)
  await modal.waitFor()
  await fitMobile(modal)
  for (const [index, room] of rooms.entries()) {
    await button(modal, editorCopy.addRoom).click()
    await modal.getByLabel(editorCopy.labelEn, { exact: true }).fill(room.en)
    await modal.getByLabel(editorCopy.labelEs, { exact: true }).fill(room.es)
    if (index === 1) require(await button(modal, editorCopy.choose(2)).isDisabled(), 'PHOTO_WAS_REUSABLE_ACROSS_ROOMS')
    await button(modal, editorCopy.choose(index + 2)).click()
    await modal.getByLabel(editorCopy.captionEn, { exact: true }).fill(room.captionEn)
    await modal.getByLabel(editorCopy.captionEs, { exact: true }).fill(room.captionEs)
  }
  await button(modal, editorCopy.preview).click()
  for (const room of rooms) {
    await modal.getByText(room.en, { exact: true }).waitFor()
    await modal.getByText(room.es, { exact: true }).waitFor()
  }
  const [staged] = await Promise.all([
    page.waitForResponse(response => endpoint(response, editorPath(f), 'PUT')),
    button(modal, editorCopy.submit).click(),
  ])
  const request = staged.request().postDataJSON(), receipt = await staged.json()
  require(staged.status() === 202 && noStore(staged) && keys(request, ['room_photo_tour'])
    && keys(receipt, ['listing_id', 'message']) && receipt.listing_id === f.listingId, 'EXACT_PRIVATE_STAGE_RECEIPT_REQUIRED')
  const proposal = exactProposal(request.room_photo_tour, f)
  await modal.getByText(editorCopy.saved, { exact: true }).waitFor()
  return proposal
}

async function approveFromAdmin(admin, f, proposal) {
  const page = admin.page
  const queue = page.waitForResponse(response => endpoint(response, reviewPath))
  await dashboardListings(admin)
  const review = await queue
  const rows = await review.json()
  require(review.status() === 200 && noStore(review) && Array.isArray(rows) && rows.length === 1, 'PRIVATE_ADMIN_QUEUE_REQUIRED')
  const row = rows[0]
  require(uuid.test(row.id) && row.listing_id === f.listingId && row.status === 'pending'
    && isDeepStrictEqual(row.current_data.images, f.images) && row.current_data.room_photo_tour === null
    && isDeepStrictEqual(row.proposed_data, { room_photo_tour: proposal }), 'EXACT_ADMIN_PENDING_REVIEW_REQUIRED')
  editId = row.id
  const reviewName = 'Review room photo tour'
  require(await button(page, 'Approve').filter({ visible: true }).count() === 0, 'QUEUE_FAST_APPROVAL_BYPASSES_VISUAL_REVIEW')
  await button(page, reviewName).click()
  const modal = dialog(page, reviewName)
  const proposed = modal.getByRole('region', { name: 'Proposed room tour', exact: true })
  const checkbox = modal.getByRole('checkbox', { name: 'I checked the photos, room assignments, and labels.', exact: true })
  const approve = button(modal, 'Approve room tour')
  await modal.waitFor()
  await fitMobile(modal)
  require(await approve.isDisabled() && !await checkbox.isChecked(), 'REVIEW_ACKNOWLEDGEMENT_NOT_REQUIRED')
  for (const image of images.slice(1)) {
    const rendered = proposed.locator(`img[src="${image.url}"]`)
    await rendered.scrollIntoViewIfNeeded()
    await page.waitForFunction(({ img, width, height }) => img.complete && img.naturalWidth === width && img.naturalHeight === height,
      { img: await rendered.elementHandle(), width: image.width, height: image.height })
  }
  require(await proposed.locator('img').count() === 2 && await approve.isDisabled(), 'REVIEW_PHOTOS_OR_APPROVAL_STATE_INVALID')
  await checkbox.check()
  const [approved] = await Promise.all([
    page.waitForResponse(response => endpoint(response, approvalPath(editId), 'POST')),
    approve.click(),
  ])
  require(approved.status() === 204 && noStore(approved)
    && isDeepStrictEqual(approved.request().postDataJSON(), { room_photo_tour_reviewed: true }), 'EXPLICIT_ADMIN_APPROVAL_RECEIPT_REQUIRED')
  await modal.waitFor({ state: 'hidden' })
}

async function fullImage(page, modal, image, caption) {
  const figure = modal.locator('figure')
  const photo = figure.getByRole('img', { name: caption, exact: true })
  await photo.waitFor()
  await page.waitForFunction(({ img, width, height }) => img.complete && img.naturalWidth === width && img.naturalHeight === height,
    { img: await photo.elementHandle(), width: image.width, height: image.height })
  await photo.scrollIntoViewIfNeeded()
  require(await photo.evaluate((element, expected) => {
    const rect = element.getBoundingClientRect()
    const imageRatio = element.naturalWidth / element.naturalHeight
    return element.getAttribute('src') === expected && getComputedStyle(element).objectFit === 'contain'
      && Math.abs(rect.width / rect.height - imageRatio) < 0.03 && rect.left >= -1 && rect.right <= innerWidth + 1
      && rect.top >= -1 && rect.bottom <= innerHeight + 1
  }, image.url), 'PUBLIC_MOBILE_PHOTO_IS_CROPPED_OR_OVERFLOWING')
  require(await figure.locator('figcaption').innerText() === caption, 'PUBLIC_LOCALIZED_CAPTION_REQUIRED')
}

async function publicRoomViewer(guest, f, tour) {
  const { page, lang } = guest, locale = copy[lang]
  await openPublic(guest, f, tour)
  await flatCover(guest, f)
  const trigger = button(page, locale.open)
  await trigger.click()
  const modal = dialog(page, locale.title)
  await modal.waitFor()
  await fitMobile(modal)
  await page.waitForFunction(element => element === document.activeElement, await button(modal, locale.close).elementHandle())
  const focusReturns = async locator => {
    await page.waitForFunction(element => element === document.activeElement, await locator.elementHandle())
  }
  const roomButton = index => button(modal.getByRole('region', { name: rooms[index][lang], exact: true }), locale.photo(rooms[index][lang]))
  for (const [index, room] of rooms.entries()) {
    const thumbnail = roomButton(index)
    await thumbnail.click()
    const caption = lang === 'es' ? room.captionEs : room.captionEn
    await fullImage(page, modal, images[index + 1], caption)
    await focusReturns(button(modal, locale.back))
    require(await button(modal, locale.previous).getAttribute('aria-disabled') === 'true'
      && await button(modal, locale.next).getAttribute('aria-disabled') === 'true', 'SINGLE_PHOTO_ROOM_NAVIGATION_BOUNDARY_REQUIRED')
    await page.keyboard.press('ArrowRight')
    await fullImage(page, modal, images[index + 1], caption)
    if (index === 0) {
      await button(modal, locale.back).click()
      await focusReturns(thumbnail)
    }
  }
  if (lang === 'en') await page.keyboard.press('Escape')
  else await button(modal, locale.close).click()
  await modal.waitFor({ state: 'hidden' })
  await focusReturns(trigger)
  result.public_languages_verified += 1
}

async function assertFlatPhoto(page, lightbox, index) {
  const image = images[index]
  const photo = lightbox.locator(`:scope > img[src="${image.url}"]`)
  await photo.waitFor()
  await lightbox.getByText(`${index + 1} / ${images.length}`, { exact: true }).waitFor()
  await page.waitForFunction(({ img, width, height }) => img.complete && img.naturalWidth === width && img.naturalHeight === height,
    { img: await photo.elementHandle(), width: image.width, height: image.height })
  require(await photo.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return getComputedStyle(element).objectFit === 'contain' && bounds.width > 0 && bounds.height > 0
      && Math.abs(bounds.width / bounds.height - element.naturalWidth / element.naturalHeight) < 0.03
      && bounds.left >= -1 && bounds.right <= innerWidth + 1 && bounds.top >= -1 && bounds.bottom <= innerHeight + 1
  }), 'FLAT_GALLERY_PHOTO_IS_CROPPED_OR_OVERFLOWING')
}

async function verifyShortGallery({ page, lang }, f) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible')
  const tile = index => button(page, copy[lang].galleryTile(index, f.images.length))
  const grid = tile(0).locator('xpath=..')
  await fitMobile(grid)
  require(await grid.locator(':scope > *').count() === 3 && await grid.locator(':scope > button').count() === 3,
    'SHORT_GALLERY_MUST_HAVE_EXACTLY_THREE_TILES')
  require(await grid.evaluate(element => element.scrollWidth <= element.clientWidth + 1
    && element.scrollHeight <= element.clientHeight + 1), 'SHORT_GALLERY_GRID_OVERFLOW')

  const lightbox = page.locator('div.fixed.inset-0').filter({ has: page.locator(`img[alt=""][src^="${photoBase}"]`) })
  const previous = lightbox.locator('button:has(svg.lucide-chevron-left)')
  const next = lightbox.locator('button:has(svg.lucide-chevron-right)')
  for (const [index, expectedUrl] of f.images.entries()) {
    const photoTile = tile(index)
    await photoTile.waitFor()
    await fitMobile(photoTile)
    require(await photoTile.evaluate((element, url) => element.style.backgroundImage.includes(url), expectedUrl),
      'SHORT_GALLERY_TILE_POINTS_TO_WRONG_PHOTO')
    await photoTile.click()
    await assertFlatPhoto(page, lightbox, index)
    await fitMobile(lightbox)
    require(await previous.count() === Number(index > 0) && await next.count() === Number(index < f.images.length - 1),
      'SHORT_GALLERY_NAVIGATION_BOUNDARIES_WRONG')

    if (index > 0) {
      await previous.click()
      await assertFlatPhoto(page, lightbox, index - 1)
      await next.click()
      await assertFlatPhoto(page, lightbox, index)
    }
    if (index < f.images.length - 1) {
      await next.click()
      await assertFlatPhoto(page, lightbox, index + 1)
      await previous.click()
      await assertFlatPhoto(page, lightbox, index)
    }
    await page.keyboard.press('Escape')
    await lightbox.waitFor({ state: 'detached' })
    await photoTile.waitFor()
    result.short_gallery_tiles_verified += 1
  }
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), f = fixture.roomTour
  const synthetic = new Map(images.map(image => [image.url, syntheticPng(image)]))
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let proposal, publicEn, publicEs
  try {
    await step('anonymous_unpublished_flat_gallery_before', async () => {
      const guest = await session(browser, fixture, 'guest', 'en', synthetic)
      await directPublic(guest, f, null)
      await openPublic(guest, f, null)
      await flatCover(guest, f)
      const outsider = await session(browser, fixture, 'outsider', 'en', synthetic)
      const denied = await outsider.context.request.get(api + editorPath(f), { timeout: wait, maxRedirects: 0 })
      try {
        const body = await denied.json()
        require(denied.status() === 404 && noStore(denied) && keys(body, ['detail']) && body.detail === 'Listing not found',
          'UNAUTHORIZED_EDITOR_EXPOSED_LISTING')
      } finally { await denied.dispose() }
    })

    await step('spanish_mobile_owner_stages_two_rooms', async () => {
      proposal = await stageFromOwner(await session(browser, fixture, 'owner', 'es', synthetic), f)
    })

    await step('anonymous_pending_keeps_public_gallery', async () => {
      const guest = await session(browser, fixture, 'guest', 'es', synthetic)
      await directPublic(guest, f, null)
      await openPublic(guest, f, null)
      await flatCover(guest, f)
    })

    await step('admin_explicit_visual_review_publishes', async () => {
      await approveFromAdmin(await session(browser, fixture, 'admin', 'en', synthetic), f, proposal)
    })

    await step('bilingual_public_mobile_room_viewer', async () => {
      publicEn = await session(browser, fixture, 'guest', 'en', synthetic)
      await directPublic(publicEn, f, proposal)
      await publicRoomViewer(publicEn, f, proposal)
      publicEs = await session(browser, fixture, 'guest', 'es', synthetic)
      await publicRoomViewer(publicEs, f, proposal)
    })

    await step('bilingual_mobile_short_gallery_all_photos', async () => {
      for (const guest of [publicEn, publicEs]) await verifyShortGallery(guest, f)
    })

    await step('only_exact_loopback_writes_and_synthetic_media', async () => {
      result.blocked_map_frames = blockedMapFrames
      result.unexpected_external_navigations = unexpectedExternalNavigation
      require(result.stage_writes === 1 && result.approval_writes === 1 && result.anonymous_phases_verified === 3
        && result.synthetic_images_verified === 3 && result.public_languages_verified === 2 && result.short_gallery_tiles_verified === 6
        && result.blocked_listing_view_posts >= 1
        && result.blocked_listing_view_posts <= 20 && result.unexpected_browser_mutations === 0
        && result.blocked_external_requests <= 100 && blockedMapFrames <= result.blocked_external_requests
        && unexpectedExternalNavigation === 0, 'EXACT_SYNTHETIC_PUBLICATION_SCOPE_REQUIRED')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try {
  await main()
} catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_FAILURE'
  const frames = [...String(error?.stack ?? '').matchAll(/run-integrated-room-tour-browser\.mjs:(\d+):\d+/g)]
  const source = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  result.source_line = source ? Number(source[1]) : null
  process.exitCode = 1
}
console.log('INTEGRATED_ROOM_TOUR_BROWSER_RESULT ' + JSON.stringify(result))
