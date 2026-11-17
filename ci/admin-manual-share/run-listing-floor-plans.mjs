import { createHash } from 'node:crypto'
import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918'
const dashboard = 'http://127.0.0.1:4175'
const landing = 'http://127.0.0.1:8917'
const listingId = '10000000-0000-0000-0000-000000000001'
const listingTitle = 'Synthetic Floor Plan Villa'
const listingPath = '/listing/' + listingId + '/'
const adminPath = '/admin/listings/' + listingId + '/floor-plan'
const publicPath = '/listings/' + listingId + '/floor-plan'
const fixtureFile = '/browser-fixtures/fixtures.json'
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/
const english = { title: 'Property floor plan', view: 'View floor plan', close: 'Close floor plan' }
const spanish = { title: 'Plano de la propiedad', view: 'Ver plano', close: 'Cerrar plano' }
const checks = []
let stage = 'fixture'

class CheckFailure extends Error {}
function check(pass, code) {
  if (!pass) throw new CheckFailure(code)
}

function failureCode(error) {
  if (error instanceof CheckFailure) return error.message
  if (String(error?.message).includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  return error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_NOT_FOUND' : 'BROWSER_OPERATION_FAILED'
}

function sha(bytes) { return createHash('sha256').update(bytes).digest('hex') }

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixtureFile, 'HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_ONLY_REQUIRED')
  const stat = await lstat(fixtureFile)
  check(stat.isFile() && (stat.mode & 0o777) === 0o600 && stat.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixtureFile, 'utf8'))
  check(fixture.listing_id === listingId && fixture.listing_title === listingTitle, 'EXACT_SYNTHETIC_LISTING_REQUIRED')
  for (const name of ['admin', 'owner']) {
    const user = fixture.accounts?.[name]
    check(user?.email === 'browser-' + name + '@example.com'
      && /^[a-zA-Z0-9_-]{32}$/.test(user.password), 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  }
  const png = Buffer.from(fixture.original_png, 'base64')
  const jpeg = Buffer.from(fixture.replacement_jpeg, 'base64')
  check(png.length > 32 && png.length < 2048 && png.subarray(0, 8).toString('hex') === '89504e470d0a1a0a'
    && jpeg.length > 32 && jpeg.length < 2048 && jpeg.subarray(0, 3).toString('hex') === 'ffd8ff', 'SYNTHETIC_IMAGES_REQUIRED')
  return {
    admin: fixture.accounts.admin, owner: fixture.accounts.owner,
    original: { mimeType: 'image/png', name: 'synthetic-original.png', buffer: png, width: 64, height: 48 },
    replacement: { mimeType: 'image/jpeg', name: 'synthetic-replacement.jpg', buffer: jpeg, width: 72, height: 52 },
  }
}

function apiResponse(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function current(response) {
  check((response.headers()['cache-control'] ?? '').split(',').some(token => token.trim() === 'no-store'),
    'UNCACHED_ASSET_RESPONSE_REQUIRED')
}

function request(context, path, method = 'GET', data) {
  return context.request.fetch(api + path, { method, data, maxRedirects: 0, timeout: 8_000, headers: { Origin: dashboard } })
}

async function status(context, path, expected, method = 'GET', data) {
  const response = await request(context, path, method, data)
  try {
    current(response)
    check(response.status() === expected, 'EXACT_SCOPED_ACCESS_STATUS_REQUIRED')
    return await response.json()
  } finally {
    await response.dispose()
  }
}

async function session(browser, { account, locale = 'en', mobile = false } = {}) {
  const context = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 },
    hasTouch: mobile, isMobile: mobile, serviceWorkers: 'block',
  })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return [api, dashboard, landing].includes(url.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(language => {
    localStorage.setItem('ildr_lang', language)
    sessionStorage.setItem('lead_modal_dismissed', '1')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, locale)
  if (account) {
    const response = await request(context, '/auth/login', 'POST', account)
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'SYNTHETIC_SESSION_REQUIRED')
    await response.dispose()
  }
  return context
}

const control = (node, role, label) => node.getByRole(role, { name: label, exact: true })
const button = (node, label) => control(node, 'button', label)
const permission = node => control(node, 'checkbox', 'I confirm the property owner approved publishing this floor plan')

async function openAdmin(context) {
  const page = await context.newPage()
  await page.bringToFront()
  const [identity] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, '/auth/me')),
    page.goto(dashboard + '/listings?workspace=admin', { waitUntil: 'domcontentloaded' }),
  ])
  check(identity.status() === 200 && (await identity.json()).role === 'admin', 'EXACT_ADMIN_IDENTITY_REQUIRED')
  const row = page.locator('div[class~="sm:grid"]').filter({ has: page.getByText(listingTitle, { exact: true }) })
    .filter({ has: button(page, 'Listing actions') })
  await button(row, 'Listing actions').click()
  await button(row, 'Edit listing').click()
  await control(page, 'heading', 'Edit Listing').waitFor()
  const editor = control(page, 'region', 'Listing floor plan')
  const [initial] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, adminPath)),
    button(editor, 'Manage floor plan').click(),
  ])
  current(initial)
  const empty = await initial.json()
  check(initial.ok() && empty.draft === null && empty.published === null, 'NEW_EDITOR_MUST_START_EMPTY')
  return { page, editor }
}

function covers(asset, bytes) {
  return asset && uuid.test(asset.id) && asset.content_type === bytes.mimeType
    && Object.keys(asset).sort().join(',') === 'byte_count,content_type,created_at,id,sha256'
    && asset.byte_count === bytes.buffer.length && asset.sha256 === sha(bytes.buffer) && Number.isFinite(Date.parse(asset.created_at))
}

async function stagePlan(page, editor, upload, expectedPublished) {
  const draft = control(editor, 'region', 'Private floor plan draft')
  const { name, mimeType, buffer } = upload
  await draft.getByLabel('Choose floor plan file', { exact: true }).setInputFiles({ name, mimeType, buffer })
  const [staged] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, adminPath + '/draft', 'PUT')),
    button(draft, 'Stage floor plan').click(),
  ])
  current(staged)
  check(staged.status() === 200 && staged.request().headers()['content-type'] === upload.mimeType, 'RAW_SYNTHETIC_UPLOAD_REQUIRED')
  const state = await staged.json()
  check(covers(state.draft, upload) && (state.published?.id ?? null) === expectedPublished, 'EXACT_STAGING_RECEIPT_REQUIRED')
  await editor.getByText('Floor plan staged for review.', { exact: true }).waitFor()
  return state.draft
}

async function approvedBytes(response, expected) {
  current(response)
  const headers = response.headers()
  const ext = expected.mimeType === 'image/png' ? 'png' : 'jpg'
  check(response.status() === 200 && headers['content-type'] === expected.mimeType
    && headers['content-disposition'] === 'inline; filename="floor-plan.' + ext + '"'
    && headers['x-content-type-options'] === 'nosniff'
    && sha(await response.body()) === sha(expected.buffer), 'EXACT_VERIFIED_IMAGE_REQUIRED')
}

async function shownImage(region, label, expected) {
  const image = control(region, 'img', label)
  await image.waitFor()
  check(await image.evaluate(async (element, [width, height]) => {
    await element.decode()
    return element.src.startsWith('blob:') && element.naturalWidth === width && element.naturalHeight === height
  }, [expected.width, expected.height]), 'EXACT_APPROVED_BLOB_IMAGE_REQUIRED')
}

async function openVisitor(page, statusCode) {
  await page.bringToFront()
  const [metadata, route] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, publicPath)),
    page.goto(landing + listingPath, { waitUntil: 'domcontentloaded' }),
  ])
  current(metadata)
  const headers = await metadata.request().allHeaders()
  check(route?.status() === 200 && metadata.status() === statusCode && !headers.cookie && !headers.authorization,
    'REAL_ANONYMOUS_STATIC_DETAIL_REQUIRED')
  await control(page, 'heading', listingTitle).waitFor()
  return metadata.json()
}

async function viewVisitor(page, copy, published, expected) {
  const meta = await openVisitor(page, 200)
  check(meta.id === published.id && covers(meta, expected), 'CURRENT_PUBLIC_REVISION_REQUIRED')
  const region = control(page, 'region', copy.title)
  const open = button(region, copy.view)
  const [content] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, publicPath + '/' + published.id + '/content')),
    open.click(),
  ])
  await approvedBytes(content, expected)
  await shownImage(region, copy.title, expected)
  const dimensions = await page.evaluate(() => ({
    screen: document.documentElement.clientWidth, content: document.documentElement.scrollWidth, touch: navigator.maxTouchPoints,
  }))
  const box = await open.boundingBox()
  check(dimensions.screen === 390 && dimensions.content <= 391 && dimensions.touch > 0
    && box && box.x >= -1 && box.x + box.width <= 391 && box.height >= 43, 'MOBILE_IMAGE_CONTROL_REQUIRED')
  await button(region, copy.close).click()
  check(!await control(region, 'img', copy.title).isVisible()
    && await open.evaluate(button => document.activeElement === button), 'CLOSED_PREVIEW_MUST_RESTORE_FOCUS')
}

async function publish(page, editor, asset, previous) {
  await permission(editor).check()
  const [response] = await Promise.all([
    page.waitForResponse(reply => apiResponse(reply, adminPath + '/publish', 'POST')),
    button(editor, 'Publish floor plan').click(),
  ])
  current(response)
  const body = response.request().postDataJSON()
  const state = await response.json()
  check(response.status() === 200 && body.draft_id === asset.id && body.expected_published_id === previous
    && body.owner_permission_confirmed === true && state.draft === null && state.published?.id === asset.id
    && state.published.sha256 === asset.sha256, 'EXPLICIT_EXACT_PUBLICATION_REQUIRED')
  await editor.getByText('Floor plan published.', { exact: true }).waitFor()
}

async function run(browser, fixture) {
  stage = 'auth'
  const admin = await session(browser, { account: fixture.admin })
  const owner = await session(browser, { account: fixture.owner })
  const publicEn = await session(browser, { mobile: true })
  const publicEs = await session(browser, { locale: 'es', mobile: true })
  const enPage = await publicEn.newPage()
  const esPage = await publicEs.newPage()

  stage = 'admin_entry'
  const { page, editor } = await openAdmin(admin)
  await status(owner, adminPath, 403)
  await status(publicEn, adminPath, 401)

  stage = 'stage_preview'
  const first = await stagePlan(page, editor, fixture.original, null)
  const firstContent = '/' + first.id + '/content'
  const [preview] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, adminPath + '/draft' + firstContent)),
    button(editor, 'Preview draft').click(),
  ])
  await approvedBytes(preview, fixture.original)
  await shownImage(editor, 'Draft property floor plan', fixture.original)
  await button(editor, 'Close preview').click()
  checks.push('normal_admin_edit_stages_and_previews_exact_private_png')

  stage = 'deny_before_approval'
  check(await button(editor, 'Publish floor plan').isDisabled(), 'OWNER_PERMISSION_REQUIRED_IN_EDITOR')
  await status(admin, adminPath + '/publish', 422, 'POST', { draft_id: first.id, expected_published_id: null })
  await status(admin, adminPath + '/publish', 422, 'POST', {
    draft_id: first.id, expected_published_id: null, owner_permission_confirmed: false,
  })
  await openVisitor(enPage, 404)
  check(await button(enPage, english.view).count() === 0, 'DRAFT_ACTION_MUST_NOT_APPEAR')
  await status(publicEn, publicPath + firstContent, 404)
  await status(publicEn, adminPath + '/draft' + firstContent, 401)
  await status(owner, adminPath + '/draft' + firstContent, 403)
  checks.push('draft_hidden_from_visitor_and_owner_permission_enforced')

  stage = 'publish_first'
  await page.bringToFront()
  await publish(page, editor, first, null)
  checks.push('attestation_publishes_same_approved_asset_revision')

  stage = 'public_bilingual'
  await viewVisitor(enPage, english, first, fixture.original)
  await viewVisitor(esPage, spanish, first, fixture.original)
  checks.push('english_and_spanish_public_mobile_load_only_approved_bytes')

  stage = 'stage_replacement'
  await page.bringToFront()
  const replacement = await stagePlan(page, editor, fixture.replacement, first.id)
  const replacementContent = '/' + replacement.id + '/content'
  check(replacement.id !== first.id && replacement.sha256 !== first.sha256 && !await permission(editor).isChecked(),
    'REPLACEMENT_REQUIRES_FRESH_ATTESTATION')
  await viewVisitor(enPage, english, first, fixture.original)
  await status(publicEn, publicPath + replacementContent, 404)
  checks.push('staging_replacement_keeps_prior_public_and_replacement_private')

  stage = 'withdraw'
  await page.bringToFront()
  await button(editor, 'Withdraw published floor plan').click()
  const [withdrawn] = await Promise.all([
    page.waitForResponse(response => apiResponse(response, adminPath + '/withdraw', 'POST')),
    button(editor, 'Confirm withdrawal').click(),
  ])
  current(withdrawn)
  const next = await withdrawn.json()
  check(withdrawn.ok() && withdrawn.request().postDataJSON().published_id === first.id
    && next.published === null && next.draft?.id === replacement.id, 'WITHDRAWAL_PRESERVES_INDEPENDENT_DRAFT')
  await editor.getByText('Floor plan withdrawn.', { exact: true }).waitFor()
  await openVisitor(esPage, 404)
  check(await button(esPage, spanish.view).count() === 0, 'WITHDRAWAL_REMOVES_PUBLIC_ACTION')
  await status(publicEs, publicPath + firstContent, 404)
  await status(publicEs, publicPath + replacementContent, 404)
  checks.push('withdraw_revokes_old_public_url_and_preserves_private_draft')

  stage = 'publish_replacement'
  await page.bringToFront()
  await publish(page, editor, replacement, null)
  await viewVisitor(esPage, spanish, replacement, fixture.replacement)
  await status(publicEn, publicPath + firstContent, 404)
  check((await publicEn.cookies(api)).length === 0 && (await publicEs.cookies(api)).length === 0, 'ANONYMOUS_BROWSERS_REQUIRED')
  checks.push('replacement_publication_never_restores_withdrawn_old_url')
  for (const context of [admin, owner, publicEn, publicEs]) await context.close()
}

let browser
try {
  const fixture = await readFixture()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
  await run(browser, fixture)
  console.log('LISTING_FLOOR_PLAN_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
} catch (error) {
  console.log('LISTING_FLOOR_PLAN_BROWSER_FAILURE ' + stage + ' ' + failureCode(error))
  process.exitCode = 1
} finally {
  await browser?.close()
}
