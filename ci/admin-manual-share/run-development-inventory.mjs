import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const listingTitle = /^synthetic other owner property$/i
const englishName = 'Synthetic Development Inventory'
const spanishName = 'Inventario de desarrollo sintético'
const updatedName = 'Synthetic Inventory Draft Update'
const source = 'PRIVATE_SYNTHETIC_DEVELOPER_REFERENCE'
const note = 'PRIVATE_SYNTHETIC_DEVELOPER_NOTE'
const floorPlan = 'https://example.org/synthetic-approved-floor-plan.pdf'
const privateFloorPlan = 'https://example.org/PRIVATE_SYNTHETIC_FLOOR_PLAN.pdf'
const checks = []
let step = 'fixture'

class CheckFailure extends Error {}
function check(condition, reason) {
  if (!condition) throw new CheckFailure(reason)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const info = await lstat(fixturePath)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(['admin', 'buyer'].every(role => data.accounts?.[role]?.email === `browser-${role}@example.com`
    && /^[a-zA-Z0-9_-]{32}$/.test(data.accounts[role].password)), 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  check(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(data.foreignListingId),
    'OWNED_SYNTHETIC_LISTING_REQUIRED')
  return data
}

function fromApi(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function noCache(response) {
  check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
}

function call(context, path, method = 'GET', data = undefined) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
}

async function apiResult(context, path, expected = 200, method = 'GET', body = undefined) {
  const result = await call(context, path, method, body)
  try {
    check(result.status() === expected, 'INVENTORY_API_STATE_MISMATCH')
    noCache(result)
    return await result.json()
  } finally {
    await result.dispose()
  }
}

async function session(browser, viewport, account = null) {
  const context = await browser.newContext({ viewport, locale: 'en-US', serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return [dashboard, api].includes(target.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(() => {
    if (window.location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', 'en')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    const result = await call(context, '/auth/login', 'POST', account)
    try {
      check(result.status() === 200 && (await context.cookies(api)).some(cookie =>
        cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_SYNTHETIC_SESSION_REQUIRED')
    } finally {
      await result.dispose()
    }
  }
  return context
}

function dateInDominicanRepublic(date) {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Santo_Domingo', year: 'numeric', month: '2-digit', day: '2-digit',
  })
  const parts = Object.fromEntries(formatter.formatToParts(date).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}

const label = (scope, name) => scope.getByLabel(name, { exact: true })
const button = (scope, name) => scope.getByRole('button', { name, exact: true })

async function openFromListings(context, path, mobile) {
  const page = await context.newPage()
  const identity = page.waitForResponse(response => fromApi(response, '/auth/me'))
  await page.goto(dashboard + '/listings?workspace=admin', { waitUntil: 'domcontentloaded' })
  const user = await identity
  check(user.ok() && (await user.json()).role === 'admin', 'EXACT_ADMIN_SESSION_REQUIRED')
  const title = page.getByText(listingTitle).filter({ visible: true })
  await title.waitFor({ state: 'visible' })
  if (!mobile) {
    const row = title.locator('xpath=ancestor::div[.//button[@aria-label="Listing actions"]][1]')
    await button(row, 'Listing actions').click()
  }
  const opening = page.waitForResponse(response => fromApi(response, path))
  await button(page, 'Development inventory').click()
  const inventory = await opening
  check(inventory.ok(), 'LISTING_ENTRY_MUST_OPEN_EXACT_INVENTORY')
  noCache(inventory)
  await label(page, 'Project name (English)').waitFor({ state: 'visible' })
  return page
}

async function save(page, path) {
  const waiting = page.waitForResponse(response => fromApi(response, path + '/draft', 'PUT'))
  await button(page, 'Save draft').click()
  const response = await waiting
  check(response.status() === 200, 'DRAFT_SAVE_REQUIRED')
  noCache(response)
  return response.json()
}

async function act(page, path, operation, control, confirmation) {
  await button(page, control).click()
  const waiting = page.waitForResponse(response => fromApi(response, path + '/' + operation, 'POST'))
  await button(page, confirmation).click()
  const response = await waiting
  check(response.status() === 200, 'CONFIRMED_INVENTORY_ACTION_REQUIRED')
  noCache(response)
  return response.json()
}

async function enterInventory(page, verified, cutoff) {
  await label(page, 'Project name (English)').fill(englishName)
  await label(page, 'Project name (Spanish)').fill(spanishName)
  await label(page, 'Project summary (English)').fill('Synthetic developer-supplied units.')
  await label(page, 'Project summary (Spanish)').fill('Unidades de prueba.')
  await label(page, 'Delivery target').fill('2032-Q3')
  await label(page, 'Developer source reference').fill(source)
  await label(page, 'Verified on').fill(verified)
  await label(page, 'Availability valid through').fill(cutoff)
  await label(page, 'Private note').fill(note)
  await button(page, 'Add phase').click()
  const phase = page.getByRole('group', { name: 'Phase 1', exact: true })
  await label(phase, 'Phase code').fill('A')
  await label(phase, 'Phase name (English)').fill('Phase A')
  await label(phase, 'Phase name (Spanish)').fill('Fase A')
  await button(phase, 'Add unit').click()
  const unit = phase.getByRole('group', { name: 'Unit 1', exact: true })
  await label(unit, 'Unit code').fill('A1')
  await label(unit, 'Floor').fill('Ground floor')
  await label(unit, 'Size (m²)').fill('110.25')
  await label(unit, 'Price').fill('199000.00')
  await label(unit, 'Currency').selectOption('USD')
  await label(unit, 'Unit status').selectOption('available')
  await label(unit, 'Floor plan URL').fill(floorPlan)
  await label(unit, 'Approved for public sharing').check()
  await button(phase, 'Add unit').click()
  const second = phase.getByRole('group', { name: 'Unit 2', exact: true })
  await label(second, 'Unit code').fill('A2')
  await label(second, 'Unit status').selectOption('reserved')
  await label(second, 'Floor plan URL').fill(privateFloorPlan)
}

async function main() {
  const fixture = await readFixture()
  const path = '/admin/developments/' + fixture.foreignListingId
  const publicPath = '/listings/' + fixture.foreignListingId + '/development'
  const today = dateInDominicanRepublic(new Date())
  const cutoff = dateInDominicanRepublic(new Date(Date.now() + 172_800_000))
  const desktop = { width: 1280, height: 900 }
  const narrow = { width: 390, height: 844 }
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'admin_and_buyer_scope'
    const admin = await session(browser, desktop, fixture.accounts.admin)
    const buyer = await session(browser, narrow, fixture.accounts.buyer)
    const anonymous = await session(browser, narrow)
    await apiResult(anonymous, path, 401)
    await apiResult(buyer, path, 403)
    await apiResult(buyer, path + '/draft', 403, 'PUT', {
      expected_version: 0, project: { phases: [], payment_stages: [], updates: [] }, evidence: {},
    })
    const outsideAnchor = '/admin/developments/00000000-0000-4000-8000-000000000000/draft'
    await apiResult(admin, outsideAnchor, 405, 'PUT', {})
    const empty = await apiResult(admin, path)
    check(empty.version === 0 && empty.draft === null && empty.published === null, 'EMPTY_INVENTORY_REQUIRED')
    await apiResult(anonymous, publicPath, 404)
    checks.push('exact_synthetic_admin_buyer_and_write_scope')

    step = 'desktop_create_draft'
    const page = await openFromListings(admin, path, false)
    await enterInventory(page, today, cutoff)
    const initial = await save(page, path)
    check(initial.draft_revision === 1 && initial.reviewed_revision === null && initial.published_revision === null
      && initial.draft.project.name_en === englishName && initial.draft.project.name_es === spanishName
      && initial.draft.evidence.source_reference === source && initial.draft.evidence.private_note === note
      && initial.draft.project.phases[0].units.length === 2, 'SYNTHETIC_BILINGUAL_DRAFT_REQUIRED')
    await apiResult(anonymous, publicPath, 404)
    checks.push('desktop_listing_action_creates_private_bilingual_inventory')

    step = 'mobile_saved_preview'
    const mobileAdmin = await session(browser, narrow, fixture.accounts.admin)
    const mobile = await openFromListings(mobileAdmin, path, true)
    const previewPath = path + '/revisions/' + initial.draft_revision
    const previewResult = mobile.waitForResponse(response => fromApi(response, previewPath))
    await button(mobile, 'Preview saved revision').click()
    const savedResult = await previewResult
    check(savedResult.status() === 200, 'ADMIN_PREVIEW_REQUIRED')
    noCache(savedResult)
    const dialog = mobile.getByRole('dialog', { name: 'Saved inventory revision 1', exact: true })
    await dialog.waitFor({ state: 'visible' })
    check(await label(dialog, 'Project name (Spanish)').inputValue() === spanishName
      && await label(dialog, 'Developer source reference').inputValue() === source,
      'SOURCE_EVIDENCE_PRIVATE_MOBILE_PREVIEW_REQUIRED')
    check(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'MOBILE_HORIZONTAL_OVERFLOW')
    await apiResult(buyer, previewPath, 403)
    await button(dialog, 'Close').click()
    checks.push('mobile_listing_action_and_read_only_saved_preview')

    step = 'verified_publication'
    const reviewed = await act(page, path, 'review', 'Review revision', 'Confirm review')
    check(reviewed.reviewed_revision === initial.draft_revision && reviewed.published_revision === null,
      'ADMIN_REVIEW_MUST_NOT_PUBLISH')
    await apiResult(anonymous, publicPath, 404)
    const published = await act(page, path, 'publish', 'Publish revision', 'Confirm publication')
    const live = await apiResult(anonymous, publicPath)
    const buyerView = await apiResult(buyer, publicPath)
    const [available, reserved] = live.project.phases[0].units
    check(published.published_revision === initial.draft_revision && live.revision === initial.draft_revision
      && live.project.name_en === englishName && live.project.name_es === spanishName
      && live.project.target_delivery === '2032-Q3' && live.verified_on === today
      && live.availability_valid_through === cutoff && live.availability_current === true
      && available.floor === 'Ground floor' && available.status === 'available' && available.currency === 'USD'
      && Number(available.price) === 199000 && Number(available.area_m2) === 110.25
      && available.floor_plan_url === floorPlan && reserved.status === 'reserved' && reserved.floor_plan_url === null
      && !JSON.stringify(live).includes('PRIVATE_SYNTHETIC') && !('evidence' in live)
      && !('floor_plan_approved' in available) && JSON.stringify(live) === JSON.stringify(buyerView),
      'EXACT_APPROVED_PUBLIC_PROJECTION_REQUIRED')
    checks.push('confirmed_review_and_publication_expose_only_approved_data')

    step = 'later_draft_preserves_publication'
    await mobile.reload({ waitUntil: 'domcontentloaded' })
    await button(mobile, 'Development inventory').click()
    await label(mobile, 'Project name (English)').fill(updatedName)
    const revised = await save(mobile, path)
    check(revised.draft_revision === 2 && revised.reviewed_revision === null && revised.published_revision === 1
      && revised.published.project.name_en === englishName && revised.draft.project.name_en === updatedName,
      'DRAFT_MUST_NOT_REPLACE_PUBLISHED_REVISION')
    const stillLive = await apiResult(anonymous, publicPath)
    check(stillLive.revision === initial.draft_revision && stillLive.project.name_en === englishName,
      'PUBLISHED_REVISION_MUST_STAY_VISIBLE')
    await apiResult(admin, path + '/withdraw', 409, 'POST', { expected_version: initial.version })
    checks.push('later_private_draft_keeps_original_public_revision_and_rejects_stale_write')

    step = 'confirmed_withdrawal'
    const removed = await act(mobile, path, 'withdraw', 'Withdraw inventory', 'Confirm withdrawal')
    check(removed.published_revision === null && removed.published === null && removed.draft_revision === 2,
      'WITHDRAWAL_MUST_KEEP_PRIVATE_DRAFT')
    await apiResult(anonymous, publicPath, 404)
    await apiResult(buyer, publicPath, 404)
    const history = await apiResult(admin, previewPath)
    check(history.project.name_en === englishName && history.evidence.source_reference === source && history.revision === 1,
      'HISTORIC_PRIVATE_EVIDENCE_REQUIRED')
    checks.push('confirmed_withdrawal_hides_public_catalog_and_retains_private_history')
    console.log('DEVELOPMENT_INVENTORY_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  console.log('DEVELOPMENT_INVENTORY_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason, checks }))
  process.exitCode = 1
}
