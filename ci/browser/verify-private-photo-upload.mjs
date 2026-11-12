import { createHash } from 'node:crypto'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const timeout = 10_000
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const digest = /^[0-9a-f]{64}$/
const alt = 'Private photo attached to this entry'
const updateText = 'Browser rehearsal uploaded orange progress photo'
const uploadPath = '/property-network/media'
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAGCAIAAABxZ0isAAAAEUlEQVR42mN4kq+CFTEMpAQAvzJGURcKCxAAAAAASUVORK5CYII=',
  'base64',
)

export class PrivatePhotoUploadFailure extends Error {}

function check(condition, code) {
  if (!condition) throw new PrivatePhotoUploadFailure(code)
}

function exactKeys(record, keys) {
  return record && typeof record === 'object' && !Array.isArray(record)
    && Object.keys(record).length === keys.length && keys.every(key => Object.hasOwn(record, key))
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function isPrivate(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache'
    && headers['x-content-type-options'] === 'nosniff'
}

function exactResponse(response, path, method) {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function readLocalJSON(page, path) {
  const response = await page.context().request.get(`${api}${path}`, { timeout, maxRedirects: 0 })
  try {
    check(response.status() === 200, 'PHOTO_UPLOAD_LOCAL_READ_REQUIRED')
    return await response.json()
  } finally {
    await response.dispose()
  }
}

async function confirmActor(page, project, account, siteRole, projectRole, canUpdate) {
  const url = new URL(page.url())
  check(url.origin === dashboard && url.pathname === '/property-network'
    && url.searchParams.get('tab') === 'projects' && url.searchParams.get('project') === project.id,
  'PHOTO_UPLOAD_SYNTHETIC_PAGE_REQUIRED')
  const user = await readLocalJSON(page, '/auth/me')
  check(user.email === account && user.role === siteRole, 'PHOTO_UPLOAD_SYNTHETIC_ACTOR_REQUIRED')
  const access = await readLocalJSON(page, `/property-network/projects/${project.id}`)
  check(access.id === project.id && access.title === project.title && access.my_role === projectRole
    && access.can_update === canUpdate, 'PHOTO_UPLOAD_PROJECT_PERMISSIONS_CHANGED')
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine, undefined, { timeout })
}

function timeline(page) {
  return page.locator('main section').filter({
    has: page.getByRole('heading', { name: 'Progress timeline', exact: true }),
  })
}

function timelineEntry(page) {
  return timeline(page).locator('ol > li').filter({ has: page.getByText(updateText, { exact: true }) })
}

async function confirmMediaRead(page, media, status) {
  const response = await page.context().request.get(`${api}${media.url}`, { timeout, maxRedirects: 0 })
  try {
    check(response.status() === status && isPrivate(response), 'PHOTO_UPLOAD_MEDIA_READ_PRIVACY_CHANGED')
    const headers = response.headers()
    if (status === 404) {
      check(headers['content-type']?.startsWith('application/json'), 'PHOTO_UPLOAD_DENIAL_CONTENT_TYPE')
      const body = await response.json()
      check(exactKeys(body, ['detail']) && body.detail === 'Photo not found', 'PHOTO_UPLOAD_UNLINKED_DATA_EXPOSED')
      return
    }
    const bytes = await response.body()
    check(headers['content-type'] === 'image/webp' && headers['content-disposition'] === 'inline'
      && bytes.length === media.bytes && bytes.subarray(0, 4).toString('ascii') === 'RIFF'
      && bytes.subarray(8, 12).toString('ascii') === 'WEBP' && sha256(bytes) === media.sha256,
    'PHOTO_UPLOAD_API_WEBP_MISMATCH')
  } finally {
    await response.dispose()
  }
}

async function confirmRenderedWebp(page, container, media) {
  const photo = container.getByRole('img', { name: alt, exact: true })
  await photo.waitFor({ state: 'visible', timeout })
  await photo.scrollIntoViewIfNeeded({ timeout })
  const rendered = await photo.evaluate(async image => {
    try { await image.decode() } catch { return null }
    const bounds = image.getBoundingClientRect()
    return {
      source: image.getAttribute('src'), width: image.naturalWidth, height: image.naturalHeight,
      fits: bounds.width > 0 && bounds.left >= -1 && bounds.right <= innerWidth + 1
        && document.documentElement.scrollWidth <= innerWidth + 1,
    }
  })
  check(rendered?.source?.startsWith(`blob:${dashboard}/`) && rendered.width === media.width
    && rendered.height === media.height && rendered.fits, 'PHOTO_UPLOAD_BROWSER_RENDER_MISMATCH')
  const decoded = await page.evaluate(async source => {
    const response = await fetch(source, { redirect: 'error' })
    const data = await response.arrayBuffer()
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', data))
    return { type: response.headers.get('content-type'), length: data.byteLength,
      sha256: Array.from(hash, byte => byte.toString(16).padStart(2, '0')).join('') }
  }, rendered.source)
  check(decoded.type === 'image/webp' && decoded.length === media.bytes && decoded.sha256 === media.sha256,
    'PHOTO_UPLOAD_BROWSER_BLOB_MISMATCH')
  return rendered.source
}

function linkedUpdate(record, updateId, projectId, mediaId, occurredOn) {
  return record?.id === updateId && record.project_id === projectId && record.kind === 'progress'
    && record.body === updateText && record.occurred_on === occurredOn && record.correction_of === null
    && Array.isArray(record.media_ids) && record.media_ids.length === 1 && record.media_ids[0] === mediaId
}

/** The harness supplies two already signed-in synthetic pages on the same fixture project's timeline. */
export async function verifyPrivatePhotoUpload({ ownerPage, viewerPage, project }) {
  check(project && uuid.test(project.id) && uuid.test(project.mediaId) && uuid.test(project.unattachedMediaId)
    && new Set([project.id, project.mediaId, project.unattachedMediaId]).size === 3
    && ownerPage.context() !== viewerPage.context(), 'PHOTO_UPLOAD_ISOLATED_FIXTURE_REQUIRED')
  check(sha256(png) === '3cfa23afdeeb948440fdc88bacb036f90fce7484be369b14db1e6cd984b55382',
    'PHOTO_UPLOAD_SYNTHETIC_FILE_CHANGED')
  await confirmActor(ownerPage, project, 'browser-owner@example.com', 'owner', 'owner', true)
  await confirmActor(viewerPage, project, 'browser-member@example.com', 'buyer', 'viewer', false)

  const updatesPath = `/property-network/projects/${project.id}/updates`
  const ownerWrites = []
  let viewerWrites = 0
  function mutationPath(request) {
    const url = new URL(request.url())
    return url.origin === api && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
      ? `${request.method()} ${url.pathname}` : null
  }
  function ownerRequest(request) {
    const mutation = mutationPath(request)
    if (mutation) ownerWrites.push(mutation)
  }
  function viewerRequest(request) {
    if (mutationPath(request)) viewerWrites += 1
  }
  ownerPage.on('request', ownerRequest)
  viewerPage.on('request', viewerRequest)
  try {
    await foreground(ownerPage)
    const fileLabel = 'Add private progress photos'
    const composer = timeline(ownerPage).locator('form').filter({
      has: ownerPage.getByLabel(fileLabel, { exact: true }),
    })
    const input = composer.getByLabel(fileLabel, { exact: true })
    const message = composer.getByLabel('Message', { exact: true })
    const save = composer.getByRole('button', { name: 'Save entry', exact: true })
    await input.waitFor({ state: 'visible', timeout })
    check(await save.isDisabled() && await viewerPage.getByLabel(fileLabel, { exact: true }).count() === 0,
      'PHOTO_UPLOAD_COMPOSER_PERMISSION_MISMATCH')

    const [uploaded] = await Promise.all([
      ownerPage.waitForResponse(response => exactResponse(response, uploadPath, 'POST'), { timeout }),
      input.setInputFiles({ name: 'browser-progress-fixture.png', mimeType: 'image/png', buffer: png }, { timeout }),
    ])
    check(uploaded.status() === 201 && isPrivate(uploaded)
      && (await uploaded.request().headerValue('content-type'))?.startsWith('multipart/form-data; boundary=')
      && await uploaded.request().headerValue('origin') === dashboard, 'PHOTO_UPLOAD_REAL_MULTIPART_REQUIRED')
    const media = await uploaded.json()
    check(exactKeys(media, ['id', 'url', 'content_type', 'bytes', 'sha256', 'width', 'height'])
      && typeof media.id === 'string' && uuid.test(media.id)
      && ![project.id, project.mediaId, project.unattachedMediaId].includes(media.id)
      && media.url === `${uploadPath}/${media.id}` && media.content_type === 'image/webp'
      && Number.isInteger(media.bytes) && media.bytes >= 12 && media.bytes <= 5 * 1024 * 1024
      && typeof media.sha256 === 'string' && digest.test(media.sha256) && media.sha256 !== project.mediaSha256
      && media.width === 8 && media.height === 6, 'PHOTO_UPLOAD_PRIVATE_RECEIPT_INVALID')
    await composer.getByRole('button', { name: 'Remove draft photo 1', exact: true }).waitFor({ state: 'visible', timeout })
    await confirmRenderedWebp(ownerPage, composer, media)
    await confirmMediaRead(ownerPage, media, 200)
    await confirmMediaRead(viewerPage, media, 404)

    const date = await composer.getByLabel('Date the work happened', { exact: true }).inputValue({ timeout })
    check(/^\d{4}-\d{2}-\d{2}$/.test(date) && await save.isDisabled(), 'PHOTO_UPLOAD_DRAFT_REQUIRES_MESSAGE')
    await message.fill(updateText, { timeout })
    const [saved] = await Promise.all([
      ownerPage.waitForResponse(response => exactResponse(response, updatesPath, 'POST'), { timeout }),
      save.click({ timeout }),
    ])
    check(saved.status() === 201 && isPrivate(saved), 'PHOTO_UPLOAD_ATTACH_FAILED')
    const payload = saved.request().postDataJSON()
    check(exactKeys(payload, ['submission_id', 'body', 'occurred_on', 'correction_of', 'media_ids'])
      && typeof payload.submission_id === 'string' && uuid.test(payload.submission_id)
      && payload.body === updateText && payload.occurred_on === date && payload.correction_of === null
      && Array.isArray(payload.media_ids) && payload.media_ids.length === 1 && payload.media_ids[0] === media.id,
    'PHOTO_UPLOAD_ATTACHMENT_REQUEST_MISMATCH')
    const update = await saved.json()
    check(typeof update?.id === 'string' && uuid.test(update.id)
      && linkedUpdate(update, update.id, project.id, media.id, date), 'PHOTO_UPLOAD_ATTACHMENT_RECEIPT_MISMATCH')
    await confirmRenderedWebp(ownerPage, timelineEntry(ownerPage), media)
    check(await message.inputValue({ timeout }) === '' && await save.isDisabled()
      && await composer.getByRole('img', { name: alt, exact: true }).count() === 0, 'PHOTO_UPLOAD_DRAFT_NOT_CLEARED')

    const persisted = await readLocalJSON(viewerPage, updatesPath)
    check(Array.isArray(persisted.items)
      && persisted.items.filter(row => linkedUpdate(row, update.id, project.id, media.id, date)).length === 1,
    'PHOTO_UPLOAD_LINKED_TIMELINE_READBACK_FAILED')
    await confirmMediaRead(viewerPage, media, 200)
    await foreground(viewerPage)
    const [reloadedProject] = await Promise.all([
      viewerPage.waitForResponse(response => exactResponse(response, `/property-network/projects/${project.id}`, 'GET'), { timeout }),
      viewerPage.reload({ waitUntil: 'domcontentloaded', timeout }),
    ])
    check(reloadedProject.status() === 200 && isPrivate(reloadedProject), 'PHOTO_UPLOAD_VIEWER_RELOAD_FAILED')
    await foreground(viewerPage)
    const viewerBlob = await confirmRenderedWebp(viewerPage, timelineEntry(viewerPage), media)

    check(ownerWrites.length === 2 && ownerWrites[0] === `POST ${uploadPath}`
      && ownerWrites[1] === `POST ${updatesPath}` && viewerWrites === 0, 'PHOTO_UPLOAD_UNEXPECTED_BROWSER_MUTATION')
    return {
      mediaId: media.id,
      viewerBlob,
      evidence: { upload_status: 201, attach_status: 201, unattached_viewer_status: 404,
        attached_viewer_status: 200, exact_webp_bytes: true, webp_digest: media.sha256, private_cache: true,
        owner_draft_rendered: true, owner_saved_rendered: true, viewer_saved_rendered: true, viewer_reloaded: true,
        owner_mutations: 2, viewer_mutations: 0 },
    }
  } finally {
    ownerPage.off('request', ownerRequest)
    viewerPage.off('request', viewerRequest)
  }
}
