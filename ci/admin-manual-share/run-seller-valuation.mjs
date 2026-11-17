import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const sellerName = 'Browser Seller Valuation'
const saleTitle = 'Synthetic other owner property'
const sourceLabel = 'Synthetic deed registry'
const sourceReference = 'Synthetic fixture source number 1042'
const reviewNote = 'Checked exact synthetic closing evidence.'
const reportNote = 'Synthetic reviewed range supported by recorded evidence.'
const followUpNote = 'Collect the synthetic floor plan details.'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const checks = []
let step = 'fixture'

class FailedCheck extends Error {}
function check(condition, reason) {
  if (!condition) throw new FailedCheck(reason)
}

function failureReason(error) {
  if (error instanceof FailedCheck) return error.message
  const message = typeof error?.message === 'string' ? error.message : ''
  if (message.includes('intercepts pointer events')) return 'POINTER_INTERCEPTED'
  if (message.includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  if (message.includes('element is not enabled')) return 'ELEMENT_DISABLED'
  if (message.includes('waiting for event "response"')) return 'API_RESPONSE_NOT_OBSERVED'
  if (error?.name === 'TimeoutError' && message.includes('waiting for locator') && !message.includes('locator resolved')) {
    return 'LOCATOR_NOT_FOUND'
  }
  return error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  check(file.isFile() && (file.mode & 0o777) === 0o600 && file.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(uuid.test(value.valuationLeadId) && uuid.test(value.foreignListingId) && uuid.test(value.shareLeadId)
    && new Set([value.valuationLeadId, value.foreignListingId, value.shareLeadId]).size === 3,
    'EXACT_SYNTHETIC_IDENTIFIERS_REQUIRED')
  for (const [entry, address] of [[value.accounts.admin, 'browser-admin@example.com'],
    [value.accounts.realtor, 'browser-realtor@example.com'],
    [value.unrelatedValuationAccount, 'browser-valuation-unrelated@example.com']]) {
    check(entry?.email === address && /^[a-zA-Z0-9_-]{32}$/.test(entry.password), 'SYNTHETIC_CREDENTIAL_SHAPE_REQUIRED')
  }
  return value
}

function isEndpoint(response, path, method = 'GET', query) {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
    && (query === undefined || url.searchParams.get('q') === query)
}

async function request(context, path, method = 'GET', data, status = 200, requireNoStore = true) {
  const result = await context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
  try {
    check(result.status() === status, 'EXACT_API_STATUS_REQUIRED')
    if (requireNoStore) check(result.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
    return await result.json()
  } finally {
    await result.dispose()
  }
}

async function session(browser, viewport, account) {
  const context = await browser.newContext({ viewport, locale: 'en-US', serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return [dashboard, api].includes(url.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(() => {
    if (window.location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', 'en')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    await request(context, '/auth/login', 'POST', account, 200, false)
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_LOGIN_COOKIE_REQUIRED')
  }
  return context
}

function localDate(days = 0) {
  const result = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Santo_Domingo', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(Date.now() + days * 86_400_000))
  const parts = Object.fromEntries(result.map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const dialog = (page, name) => page.getByRole('dialog', { name, exact: true })
const text = (scope, name) => scope.getByText(name, { exact: true })
const option = (scope, value) => scope.locator(`select:has(> option[value="${value}"])`).selectOption(value)
const labeled = (scope, name) => scope.getByLabel(name, { exact: true })

function captionTextarea(scope, name) {
  return scope.locator('span').and(text(scope, name)).locator('xpath=parent::label').locator('textarea')
}

async function responseFor(page, path, method, action, query) {
  const [response] = await Promise.all([page.waitForResponse(item => isEndpoint(item, path, method, query)), action()])
  check(response.ok(), 'BROWSER_API_RESPONSE_REQUIRED')
  if (path.includes('/valuation')) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
  return response.json()
}

async function clickForResponse(page, path, method, control, query) {
  await control.waitFor({ state: 'visible' })
  return responseFor(page, path, method, () => control.click(), query)
}

async function openFromLead(context, role, path) {
  step = role + '_lead_list_identity'
  const page = await context.newPage()
  await page.bringToFront()
  const identity = await responseFor(page, '/auth/me', 'GET',
    () => page.goto(`${dashboard}/leads?workspace=${role}`, { waitUntil: 'domcontentloaded' }))
  check(identity.role === role, 'EXPECTED_ROLE_REQUIRED')
  step = role + '_seller_lead_row'
  await text(page.locator('main'), sellerName).filter({ visible: true }).click()
  step = role + '_open_valuation_from_lead'
  const state = await clickForResponse(page, path, 'GET', button(page, 'Open seller valuation'))
  await page.getByRole('heading', { name: 'Seller valuation', exact: true }).waitFor({ state: 'visible' })
  check(state.lead_id === path.split('/')[3], 'EXACT_SELLER_WORKSPACE_REQUIRED')
  return { page, state }
}

async function dialogAction(page, title, path, method, submit) {
  const panel = dialog(page, title)
  const state = await clickForResponse(page, path, method, button(panel, submit))
  await panel.waitFor({ state: 'hidden' })
  return state
}

async function main() {
  const fixture = await readFixture()
  const path = '/staff/leads/' + fixture.valuationLeadId + '/valuation'
  const narrow = { width: 390, height: 844 }
  const desktop = { width: 1280, height: 900 }
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'verified_actor_and_lead_scope'
    const admin = await session(browser, desktop, fixture.accounts.admin)
    const realtor = await session(browser, narrow, fixture.accounts.realtor)
    const unrelated = await session(browser, narrow, fixture.unrelatedValuationAccount)
    const anonymous = await session(browser, narrow)
    await request(anonymous, path, 'GET', undefined, 401)
    await request(unrelated, path, 'GET', undefined, 404)
    await request(unrelated, path + '/asking-candidates', 'GET', undefined, 404)
    await request(unrelated, path + '/draft', 'PUT', { expected_version: 0, subject: {} }, 404)
    await request(admin, '/staff/leads/' + fixture.shareLeadId + '/valuation', 'GET', undefined, 404)
    await request(admin, '/staff/leads/' + fixture.shareLeadId + '/valuation/draft', 'PUT',
      { expected_version: 0, subject: {} }, 405)
    checks.push('unrelated_staff_and_anonymous_denied')

    const { page, state: empty } = await openFromLead(admin, 'admin', path)
    step = 'admin_starts_historical_valuation'
    check(empty.version === 0 && empty.started === false && empty.intake_subject === null, 'HISTORICAL_BLANK_WORKSPACE_REQUIRED')
    const started = await clickForResponse(page, path + '/draft', 'PUT', button(page, 'Start valuation'))
    check(started.version === 1 && started.started && started.intake_subject === null, 'START_HISTORICAL_VALUATION_REQUIRED')
    step = 'admin_edits_property_facts'
    await option(page, 'villa')
    await labeled(page, 'City, town, or neighborhood').fill('Cabarete')
    await labeled(page, 'Property size (m²)').fill('125.75')
    await labeled(page, 'Bedrooms').fill('3')
    await labeled(page, 'Bathrooms').fill('2.5')
    await option(page, 'good')
    step = 'admin_saves_property_facts'
    const draft = await clickForResponse(page, path + '/draft', 'PUT', button(page, 'Save property facts'))
    check(draft.version === 2 && draft.subject.property_type === 'villa' && draft.subject.condition === 'good'
      && Number(draft.subject.area_m2) === 125.75 && draft.subject.location === 'Cabarete'
      && draft.intake_subject === null, 'EXACT_HISTORICAL_PROPERTY_DRAFT_REQUIRED')
    checks.push('admin_enters_normal_seller_detail_and_saves_facts')

    const { page: agent, state: assigned } = await openFromLead(realtor, 'realtor', path)
    check(assigned.version === 2 && assigned.subject.location === draft.subject.location, 'CURRENT_ASSIGNMENT_MUST_SEE_WORKSPACE')
    check(await agent.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'MOBILE_HORIZONTAL_OVERFLOW')
    step = 'realtor_searches_asking_catalog'
    await button(agent, 'Find asking comparables').click()
    let panel = dialog(agent, 'Find asking comparables')
    await labeled(panel, 'Search listings').fill(saleTitle)
    const candidates = await clickForResponse(agent, path + '/asking-candidates', 'GET', button(panel, 'Search'), saleTitle)
    check(candidates.items.length === 1 && candidates.items[0].listing_id === fixture.foreignListingId,
      'EXACT_ACTIVE_SALE_COMPARABLE_REQUIRED')
    step = 'realtor_finds_exact_asking_row'
    const heading = panel.getByRole('heading', { name: saleTitle, exact: true })
    await heading.waitFor({ state: 'visible' })
    const row = heading.locator('xpath=ancestor::li[1]')
    step = 'realtor_saves_asking_snapshot'
    const asking = await clickForResponse(agent, path + '/comparables/asking', 'POST',
      button(row, 'Add asking comparable'))
    await panel.waitFor({ state: 'hidden' })
    check(asking.comparables.length === 1 && asking.comparables[0].kind === 'asking'
      && asking.comparables[0].listing_id === fixture.foreignListingId && Number(asking.comparables[0].price) === 1000
      && asking.comparables[0].currency === 'USD' && asking.comparables[0].sold_review === null,
      'EXACT_ASKING_SNAPSHOT_REQUIRED')
    checks.push('assigned_realtor_captures_asking_snapshot')

    step = 'realtor_enters_sold_source'
    await button(agent, 'Add sold comparable').click()
    panel = dialog(agent, 'Add sold comparable')
    const soldOn = localDate(-60)
    await option(panel, 'villa')
    await labeled(panel, 'City, town, or neighborhood').fill('Cabarete')
    await labeled(panel, 'Closed sale price').fill('245000')
    await option(panel, 'USD')
    await labeled(panel, 'Closing date').fill(soldOn)
    await labeled(panel, 'Source label').fill(sourceLabel)
    await captionTextarea(panel, 'Private source reference').fill(sourceReference)
    step = 'realtor_saves_unverified_sold_source'
    const collected = await dialogAction(agent, 'Add sold comparable', path + '/comparables/sold', 'POST', 'Save sold evidence')
    const sold = collected.comparables.find(item => item.kind === 'sold')
    check(sold?.source_reference === sourceReference && sold.closed_on === soldOn && sold.sold_review === null,
      'UNVERIFIED_SOLD_SOURCE_REQUIRED')
    step = 'realtor_verifies_sold_source'
    await button(agent, 'Verify sold evidence').click()
    await captionTextarea(dialog(agent, 'Verify sold evidence'), 'Review note').fill(reviewNote)
    const verified = await dialogAction(agent, 'Verify sold evidence', path + '/comparables/' + sold.id + '/sold-review',
      'PUT', 'Confirm verification')
    check(verified.comparables.find(item => item.id === sold.id)?.sold_review?.decision === 'verified'
      && verified.comparables.find(item => item.id === sold.id)?.sold_review?.review_note === reviewNote,
      'VERIFIED_SOLD_EVIDENCE_REQUIRED')
    checks.push('assigned_realtor_records_and_verifies_sold_evidence')

    step = 'realtor_reviews_evidence_and_range'
    await button(agent, 'Review report').click()
    panel = dialog(agent, 'Review report')
    await text(panel, '1 asking comparable').waitFor({ state: 'visible' })
    await text(panel, '1 verified sold comparable').waitFor({ state: 'visible' })
    await option(panel, 'range')
    await labeled(panel, 'Lower estimate').fill('240000')
    await labeled(panel, 'Upper estimate').fill('260000')
    await option(panel, 'USD')
    await captionTextarea(panel, 'Report notes').fill(reportNote)
    step = 'realtor_saves_reviewed_report'
    const reviewed = await dialogAction(agent, 'Review report', path + '/review', 'POST', 'Save reviewed report')
    check(reviewed.review?.outcome === 'range' && reviewed.review.status === 'current'
      && Number(reviewed.review.price_low) === 240000 && Number(reviewed.review.price_high) === 260000
      && reviewed.review.notes === reportNote, 'CURRENT_REVIEWED_RANGE_REQUIRED')
    checks.push('reviewed_range_distinguishes_asking_and_verified_sold')

    step = 'realtor_schedules_follow_up'
    await button(agent, 'Schedule follow-up').click()
    panel = dialog(agent, 'Schedule follow-up')
    const followUpDay = localDate(7)
    await labeled(panel, 'Follow-up date').fill(followUpDay)
    await captionTextarea(panel, 'Follow-up note').fill(followUpNote)
    const waiting = await dialogAction(agent, 'Schedule follow-up', path + '/follow-ups', 'POST', 'Save follow-up')
    const [task] = waiting.follow_ups
    check(waiting.review?.status === 'current' && task.follow_up_on === followUpDay
      && task.note === followUpNote && task.completed_at === null, 'PENDING_LOCAL_FOLLOW_UP_REQUIRED')
    step = 'realtor_completes_follow_up'
    await button(agent, 'Complete follow-up').click()
    const saved = await dialogAction(agent, 'Complete follow-up', path + '/follow-ups/' + task.id + '/complete',
      'POST', 'Complete follow-up')
    check(saved.follow_ups[0].completed_at && saved.review?.status === 'current', 'FOLLOW_UP_COMPLETION_REQUIRED')
    step = 'admin_rereads_completed_follow_up'
    const persisted = await request(admin, path)
    check(persisted.follow_ups[0].completed_at === saved.follow_ups[0].completed_at && persisted.review.notes === reportNote,
      'PERSISTED_PRIVATE_FOLLOW_UP_REQUIRED')
    await request(unrelated, path, 'GET', undefined, 404)
    step = 'realtor_returns_to_same_seller_lead'
    await button(agent, 'Back to lead').click()
    await button(agent, 'Open seller valuation').waitFor({ state: 'visible' })
    checks.push('private_follow_up_completed_and_report_preserved')
    console.log('SELLER_VALUATION_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  console.log('SELLER_VALUATION_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason: failureReason(error), checks }))
  process.exitCode = 1
}
