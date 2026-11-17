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
const statuses = {}
let step = 'fixture'

class CheckFailure extends Error {}
function check(condition, reason) {
  if (!condition) throw new CheckFailure(reason)
}

function safeFailure(error) {
  if (error instanceof CheckFailure) return error.message
  const message = typeof error?.message === 'string' ? error.message : ''
  if (message.includes('intercepts pointer events')) return 'POINTER_INTERCEPTED'
  if (message.includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  if (message.includes('element is not enabled')) return 'ELEMENT_DISABLED'
  if (message.includes('element is not visible')) return 'ELEMENT_HIDDEN'
  if (message.includes('element is not stable')) return 'ELEMENT_UNSTABLE'
  if (message.includes('waiting for event "response"')) return 'API_RESPONSE_NOT_OBSERVED'
  if (/\bnet::ERR_[A-Z_]+\b/.test(message)) return 'BROWSER_NETWORK_ERROR'
  if (error?.name === 'TimeoutError' && message.includes('waiting for locator') && !message.includes('locator resolved')) {
    return 'LOCATOR_NOT_FOUND'
  }
  return error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
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

const listingsReads = {
  '/auth/me': 'auth_me',
  '/admin/listing-edits': 'admin_listing_edits',
  '/admin/deal-requests': 'admin_deal_requests',
  '/admin/activity-log': 'admin_activity_log',
  '/admin/agents': 'admin_agents',
  '/admin/stats': 'admin_stats',
}

function listingReadName(response, inventoryPath) {
  const url = new URL(response.url())
  if (url.origin !== api || response.request().method() !== 'GET') return null
  if (url.pathname === inventoryPath) return 'admin_inventory'
  const revisionPrefix = inventoryPath + '/revisions/'
  if (url.pathname.startsWith(revisionPrefix) && /^[1-9][0-9]{0,9}$/.test(url.pathname.slice(revisionPrefix.length))) {
    return 'admin_inventory_preview'
  }
  if (url.pathname !== '/admin/listings') return listingsReads[url.pathname] ?? null
  if (url.searchParams.get('status') === 'pending_approval') return 'admin_listings_pending'
  if (url.searchParams.get('is_deal') === 'true') return 'admin_listings_deals'
  if (url.searchParams.get('exclude_status') === 'pending_approval') return 'admin_listings_main'
  return 'admin_listings_other'
}

function inventoryWriteName(response, inventoryPath) {
  const url = new URL(response.url())
  if (url.origin !== api) return null
  const method = response.request().method()
  if (method === 'PUT' && url.pathname === inventoryPath + '/draft') return 'inventory_draft_write'
  if (method !== 'POST') return null
  for (const action of ['review', 'publish', 'withdraw']) {
    if (url.pathname === inventoryPath + '/' + action) return 'inventory_' + action + '_write'
  }
  return null
}

// Register rejection handlers before navigation/clicks, so an earlier failed
// UI event cannot create a second, unhandled waiter timeout.
function waitForResponse(page, match) {
  return page.waitForResponse(match).then(response => ({ response }), error => ({ error }))
}

async function requireResponse(waiting) {
  const result = await waiting
  if (result.error) throw result.error
  return result.response
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

function selectUnitOption(scope, value) {
  // These native selects are inside their labels, where getByLabel includes
  // option text. Match the actual option within the exact unit fieldset.
  return scope.locator(`select:has(> option[value="${value}"])`).selectOption(value)
}

async function openFromListings(context, path, mobile) {
  const surface = mobile ? 'mobile' : 'desktop'
  step = surface + '_listings_navigation'
  const page = await context.newPage()
  page.on('response', response => {
    const name = listingReadName(response, path) ?? inventoryWriteName(response, path)
    if (name !== null) statuses[surface + '_' + name] = response.status()
  })
  const identity = waitForResponse(page, response => fromApi(response, '/auth/me'))
  const mainTable = waitForResponse(page, response => listingReadName(response, path) === 'admin_listings_main')
  await page.goto(dashboard + '/listings?workspace=admin', { waitUntil: 'domcontentloaded' })
  step = surface + '_listings_auth'
  const user = await requireResponse(identity)
  check(user.ok() && (await user.json()).role === 'admin', 'EXACT_ADMIN_SESSION_REQUIRED')
  step = surface + '_listings_main_get'
  const table = await requireResponse(mainTable)
  check(table.ok(), 'ADMIN_LISTINGS_MAIN_GET_REQUIRED')
  const listingId = path.split('/').at(-1)
  check((await table.json()).items.some(listing => listing.id === listingId && listing.status === 'active'
    && listing.transaction === 'sale'), 'EXACT_ACTIVE_SYNTHETIC_SALE_ROW_REQUIRED')
  step = surface + '_listing_title'
  const title = page.getByText(listingTitle).filter({ visible: true })
  await title.waitFor({ state: 'visible' })
  if (!mobile) {
    step = 'desktop_listing_actions_menu'
    const row = title.locator('xpath=ancestor::div[.//button[@aria-label="Listing actions"]][1]')
    await button(row, 'Listing actions').click()
  }
  step = surface + '_development_action'
  const opening = waitForResponse(page, response => fromApi(response, path))
  await button(page, 'Development inventory').click()
  step = surface + '_development_get'
  const inventory = await requireResponse(opening)
  check(inventory.ok(), 'LISTING_ENTRY_MUST_OPEN_EXACT_INVENTORY')
  noCache(inventory)
  step = surface + '_project_name_visible'
  await label(page, 'Project name (English)').waitFor({ state: 'visible' })
  return page
}

async function save(page, path, surface) {
  step = surface + '_form_validity'
  const submit = button(page, 'Save draft')
  check(await submit.evaluate(element => element.form?.checkValidity() === true), 'BROWSER_DRAFT_FORM_INVALID')
  step = surface + '_save_click'
  const waiting = waitForResponse(page, response => fromApi(response, path + '/draft', 'PUT'))
  await submit.click()
  step = surface + '_save_response'
  const response = await requireResponse(waiting)
  check(response.status() === 200, 'DRAFT_SAVE_REQUIRED')
  noCache(response)
  return response.json()
}

async function act(page, path, operation, control, confirmation) {
  step = operation + '_open_confirmation'
  await page.bringToFront()
  await button(page, control).click()
  step = operation + '_confirmation_dialog'
  const dialog = page.getByRole('dialog', { name: control, exact: true })
  await dialog.waitFor({ state: 'visible' })
  step = operation + '_confirm_click'
  const waiting = waitForResponse(page, response => fromApi(response, path + '/' + operation, 'POST'))
  await button(dialog, confirmation).click()
  step = operation + '_confirm_api_response'
  const response = await requireResponse(waiting)
  check(response.status() === 200, 'CONFIRMED_INVENTORY_ACTION_REQUIRED')
  noCache(response)
  step = operation + '_confirmation_closed'
  await dialog.waitFor({ state: 'hidden' })
  return response.json()
}

async function enterInventory(page, verified, cutoff) {
  const fill = async (scope, tag, name, value) => {
    step = 'desktop_field_' + tag
    await label(scope, name).fill(value)
  }
  await fill(page, 'project_name_en', 'Project name (English)', englishName)
  await fill(page, 'project_name_es', 'Project name (Spanish)', spanishName)
  await fill(page, 'project_summary_en', 'Project summary (English)', 'Synthetic developer-supplied units.')
  await fill(page, 'project_summary_es', 'Project summary (Spanish)', 'Unidades de prueba.')
  await fill(page, 'delivery_target', 'Delivery target', '2032-Q3')
  await fill(page, 'source_reference', 'Developer source reference', source)
  await fill(page, 'verified_on', 'Verified on', verified)
  await fill(page, 'cutoff', 'Availability valid through', cutoff)
  await fill(page, 'private_note', 'Private note', note)
  step = 'desktop_add_phase'
  await button(page, 'Add phase').click()
  const phase = page.getByRole('group', { name: 'Phase 1', exact: true })
  step = 'desktop_phase_group'
  await phase.waitFor({ state: 'visible' })
  await fill(phase, 'phase_code', 'Phase code', 'A')
  await fill(phase, 'phase_name_en', 'Phase name (English)', 'Phase A')
  await fill(phase, 'phase_name_es', 'Phase name (Spanish)', 'Fase A')
  step = 'desktop_add_unit_one'
  await button(phase, 'Add unit').click()
  const unit = phase.getByRole('group', { name: 'Unit 1', exact: true })
  step = 'desktop_unit_one_group'
  await unit.waitFor({ state: 'visible' })
  await fill(unit, 'unit_one_code', 'Unit code', 'A1')
  await fill(unit, 'floor', 'Floor', 'Ground floor')
  await fill(unit, 'size', 'Size (m²)', '110.25')
  await fill(unit, 'price', 'Price', '199000.00')
  step = 'desktop_unit_one_currency'
  await selectUnitOption(unit, 'USD')
  step = 'desktop_unit_one_status'
  await selectUnitOption(unit, 'available')
  await fill(unit, 'unit_one_floor_plan', 'Floor plan URL', floorPlan)
  step = 'desktop_approve_floor_plan'
  await label(unit, 'Approved for public sharing').check()
  step = 'desktop_add_unit_two'
  await button(phase, 'Add unit').click()
  const second = phase.getByRole('group', { name: 'Unit 2', exact: true })
  step = 'desktop_unit_two_group'
  await second.waitFor({ state: 'visible' })
  await fill(second, 'unit_two_code', 'Unit code', 'A2')
  step = 'desktop_unit_two_status'
  await selectUnitOption(second, 'reserved')
  await fill(second, 'unit_two_floor_plan', 'Floor plan URL', privateFloorPlan)
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
    const initial = await save(page, path, 'desktop')
    step = 'desktop_saved_draft_verification'
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
    step = 'mobile_preview_click'
    const previewResult = waitForResponse(mobile, response => fromApi(response, previewPath))
    await button(mobile, 'Preview saved revision').click()
    step = 'mobile_preview_api_response'
    const savedResult = await requireResponse(previewResult)
    check(savedResult.status() === 200, 'ADMIN_PREVIEW_REQUIRED')
    noCache(savedResult)
    step = 'mobile_preview_dialog'
    const dialog = mobile.getByRole('dialog', { name: 'Saved inventory revision 1', exact: true })
    await dialog.waitFor({ state: 'visible' })
    step = 'mobile_preview_spanish_field'
    const spanish = label(dialog, 'Project name (Spanish)')
    check(await spanish.inputValue() === spanishName, 'SPANISH_MOBILE_PREVIEW_REQUIRED')
    step = 'mobile_preview_source_field'
    const evidence = dialog.getByRole('group', { name: 'Private developer evidence', exact: true })
    // The native textarea's initial text also contributes to its wrapper's
    // getByLabel text. Match the exact visible caption, then its own control.
    const sourceCaption = evidence.locator('span').and(evidence.getByText('Developer source reference', { exact: true }))
    await sourceCaption.waitFor({ state: 'visible' })
    const sourceField = sourceCaption.locator('xpath=parent::label').locator('textarea')
    check(await sourceField.inputValue() === source, 'PRIVATE_SOURCE_MOBILE_PREVIEW_REQUIRED')
    step = 'mobile_preview_read_only'
    check(await spanish.isDisabled() && await sourceField.isDisabled(), 'READ_ONLY_MOBILE_PREVIEW_REQUIRED')
    step = 'mobile_preview_layout'
    check(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'MOBILE_HORIZONTAL_OVERFLOW')
    step = 'mobile_preview_buyer_denied'
    await apiResult(buyer, previewPath, 403)
    step = 'mobile_preview_close_click'
    await button(dialog, 'Close').click()
    step = 'mobile_preview_close_hidden'
    await dialog.waitFor({ state: 'hidden' })
    checks.push('mobile_listing_action_and_read_only_saved_preview')

    step = 'verified_publication'
    const reviewed = await act(page, path, 'review', 'Review revision', 'Confirm review')
    step = 'review_exact_private_revision'
    check(reviewed.reviewed_revision === initial.draft_revision && reviewed.published_revision === null,
      'ADMIN_REVIEW_MUST_NOT_PUBLISH')
    step = 'review_public_still_unavailable'
    await apiResult(anonymous, publicPath, 404)
    const published = await act(page, path, 'publish', 'Publish revision', 'Confirm publication')
    step = 'publication_anonymous_read'
    const live = await apiResult(anonymous, publicPath)
    step = 'publication_buyer_read'
    const buyerView = await apiResult(buyer, publicPath)
    const [available, reserved] = live.project.phases[0].units
    step = 'publication_exact_bilingual_revision'
    check(published.published_revision === initial.draft_revision && live.revision === initial.draft_revision
      && live.project.name_en === englishName && live.project.name_es === spanishName,
      'EXACT_PUBLIC_BILINGUAL_REVISION_REQUIRED')
    step = 'publication_delivery_and_availability'
    check(live.project.target_delivery === '2032-Q3' && live.verified_on === today
      && live.availability_valid_through === cutoff && live.availability_current === true,
      'REVIEWED_DATES_REQUIRED')
    step = 'publication_unit_facts_and_approved_floor_plan'
    check(available.floor === 'Ground floor' && available.status === 'available' && available.currency === 'USD'
      && Number(available.price) === 199000 && Number(available.area_m2) === 110.25 && available.floor_plan_url === floorPlan
      && reserved.status === 'reserved' && reserved.floor_plan_url === null,
      'REVIEWED_UNIT_FACTS_REQUIRED')
    step = 'publication_private_evidence_excluded'
    check(!JSON.stringify(live).includes('PRIVATE_SYNTHETIC') && !('evidence' in live)
      && !('floor_plan_approved' in available) && JSON.stringify(live) === JSON.stringify(buyerView),
      'PUBLIC_PROJECTION_PRIVACY_REQUIRED')
    checks.push('confirmed_review_and_publication_expose_only_approved_data')

    step = 'mobile_reload_after_publication'
    await mobile.reload({ waitUntil: 'domcontentloaded' })
    step = 'mobile_reopen_development_after_publication'
    await button(mobile, 'Development inventory').click()
    step = 'mobile_edit_name_after_publication'
    await label(mobile, 'Project name (English)').fill(updatedName)
    const revised = await save(mobile, path, 'mobile')
    step = 'mobile_saved_draft_verification'
    check(revised.draft_revision === 2 && revised.reviewed_revision === null && revised.published_revision === 1
      && revised.published.project.name_en === englishName && revised.draft.project.name_en === updatedName,
      'DRAFT_MUST_NOT_REPLACE_PUBLISHED_REVISION')
    step = 'mobile_draft_public_read_unchanged'
    const stillLive = await apiResult(anonymous, publicPath)
    check(stillLive.revision === initial.draft_revision && stillLive.project.name_en === englishName,
      'PUBLISHED_REVISION_MUST_STAY_VISIBLE')
    step = 'mobile_draft_stale_withdraw_denied'
    await apiResult(admin, path + '/withdraw', 409, 'POST', { expected_version: initial.version })
    checks.push('later_private_draft_keeps_original_public_revision_and_rejects_stale_write')

    step = 'confirmed_withdrawal'
    const removed = await act(mobile, path, 'withdraw', 'Withdraw inventory', 'Confirm withdrawal')
    step = 'withdraw_keeps_private_draft'
    check(removed.published_revision === null && removed.published === null && removed.draft_revision === 2,
      'WITHDRAWAL_MUST_KEEP_PRIVATE_DRAFT')
    step = 'withdraw_anonymous_public_read_denied'
    await apiResult(anonymous, publicPath, 404)
    step = 'withdraw_buyer_public_read_denied'
    await apiResult(buyer, publicPath, 404)
    step = 'withdraw_admin_history_kept'
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
  console.log('DEVELOPMENT_INVENTORY_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason: safeFailure(error), checks, statuses }))
  process.exitCode = 1
}
