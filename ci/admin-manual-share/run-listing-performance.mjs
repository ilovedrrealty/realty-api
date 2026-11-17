import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const title = 'Browser rehearsal villa'
const feedback = 'Synthetic owner feedback from a completed visit.'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
const day = /^\d{4}-\d{2}-\d{2}$/
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
  if (message.includes('waiting for event "response"')) return 'API_RESPONSE_NOT_OBSERVED'
  return error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  check(file.isFile() && (file.mode & 0o777) === 0o600 && file.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const raw = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(uuid.test(raw.performanceListingId) && uuid.test(raw.foreignListingId)
    && raw.performanceListingId !== raw.foreignListingId && day.test(raw.performanceRecordedDay)
    && day.test(raw.performanceWindowEnd) && raw.performanceRecordedDay < raw.performanceWindowEnd,
  'EXACT_SYNTHETIC_IDENTIFIERS_REQUIRED')
  for (const [entry, address] of [[raw.accounts.owner, 'browser-owner@example.com'],
    [raw.accounts.realtor, 'browser-realtor@example.com'],
    [raw.unrelatedValuationAccount, 'browser-valuation-unrelated@example.com']]) {
    check(entry?.email === address && /^[a-zA-Z0-9_-]{32}$/.test(entry.password), 'SYNTHETIC_CREDENTIAL_SHAPE_REQUIRED')
  }
  return raw
}

async function request(context, path, method = 'GET', data, status = 200, noStore = true) {
  const response = await context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
  try {
    check(response.status() === status, 'EXACT_API_STATUS_REQUIRED')
    if (noStore) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
    return status === 204 ? null : await response.json()
  } finally {
    await response.dispose()
  }
}

async function session(browser, account, mobile = false) {
  const context = await browser.newContext({
    locale: 'en-US', serviceWorkers: 'block', isMobile: mobile, hasTouch: mobile,
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 },
  })
  context.setDefaultTimeout(12_000)
  context.setDefaultNavigationTimeout(12_000)
  await context.route('**/*', route => [dashboard, api].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort('blockedbyclient'))
  await context.addInitScript(() => {
    if (location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', 'en')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    await request(context, '/auth/login', 'POST', account, 200, false)
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_LOGIN_COOKIE_REQUIRED')
  }
  return context
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const text = (scope, value) => scope.getByText(value, { exact: true })
const label = (scope, name) => scope.getByLabel(name, { exact: true })

async function observe(page, path, method, action, query = () => true) {
  const pending = page.waitForResponse(item => {
    const url = new URL(item.url())
    return url.origin === api && url.pathname === path && item.request().method() === method && query(url.searchParams)
  }, { timeout: 12_000 }).catch(() => null)
  await action()
  const response = await pending
  check(response, 'API_RESPONSE_NOT_OBSERVED')
  check(response.ok(), 'EXACT_API_STATUS_REQUIRED')
  if (path.includes('/performance')) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
  return response
}

async function openListing(context, role, path) {
  const page = await context.newPage()
  await page.bringToFront()
  await observe(page, '/auth/me', 'GET', () => page.goto(dashboard + '/listings?workspace=' + role,
    { waitUntil: 'domcontentloaded' }))
  await text(page.locator('main'), title).filter({ visible: true }).click()
  await observe(page, path, 'GET', () => button(page, 'Performance').filter({ visible: true }).click())
  const panel = page.getByRole('dialog', { name: 'Listing performance', exact: true })
  await panel.getByRole('heading', { name: title, exact: true }).waitFor()
  return { page, panel }
}

async function record(page, panel, path, kind, date) {
  await button(panel, 'Record performance entry').click()
  if (kind === 'owner_feedback') {
    await label(panel, 'Entry type').selectOption(kind)
    await label(panel, 'Owner-visible note').fill(feedback)
    const share = label(panel, 'Share this note with the owner')
    check(await share.evaluate(input => input.required && input.validity.valueMissing), 'EXPLICIT_SHARING_REQUIRED')
    await share.check()
  }
  await label(panel, 'Date').fill(date)
  const response = await observe(page, path + '/entries', 'POST', () => button(panel, 'Save entry').click())
  const submitted = response.request().postDataJSON()
  check(submitted.kind === kind && submitted.occurred_on === date && uuid.test(submitted.request_id),
    'EXACT_ENTRY_BODY_REQUIRED')
  if (kind === 'owner_feedback') {
    check(submitted.text === feedback && submitted.share_with_owner === true, 'EXPLICIT_SHARING_REQUIRED')
  } else {
    check(!Object.hasOwn(submitted, 'text') && !Object.hasOwn(submitted, 'share_with_owner'), 'SHOWING_DATE_ONLY_REQUIRED')
  }
  await text(panel, 'Performance entry saved.').waitFor()
}

function safeProjection(value) {
  const serialized = typeof value === 'string' ? value : JSON.stringify(value)
  check(!/S02_PRIVATE_|Synthetic private lead note sentinel|Synthetic legacy inquiry history note|@example\.com/i.test(serialized),
    'PRIVATE_MATERIAL_VISIBLE')
}

async function main() {
  const fixture = await readFixture()
  const listing = '/listings/' + fixture.performanceListingId
  const path = listing + '/performance'
  const foreign = '/listings/' + fixture.foreignListingId
  const reportDay = fixture.performanceRecordedDay
  const end = fixture.performanceWindowEnd
  const query = '?start=' + reportDay + '&end=' + end
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'scope'
    const staff = await session(browser, fixture.accounts.realtor)
    const owner = await session(browser, fixture.accounts.owner, true)
    const unrelated = await session(browser, fixture.unrelatedValuationAccount)
    const anonymous = await session(browser, null)
    const forbiddenEntry = { request_id: '668dce44-355f-4f58-8e21-960c248c4a3d', occurred_on: reportDay, kind: 'completed_showing' }
    await request(anonymous, path, 'GET', undefined, 401)
    await request(owner, foreign + '/performance', 'GET', undefined, 404)
    await request(unrelated, path, 'GET', undefined, 404)
    await request(owner, path + '/entries', 'POST', forbiddenEntry, 403)
    await request(unrelated, path + '/entries', 'POST', forbiddenEntry, 404)
    await request(staff, foreign + '/performance/entries', 'POST', forbiddenEntry, 405)

    step = 'public_view'
    const cold = await request(owner, path + query)
    check(cold.listing_id === fixture.performanceListingId && cold.views.lifetime === 9
      && cold.views.range_total === null && cold.views.tracked_from === null && cold.views.coverage === 'unavailable'
      && cold.totals.confirmed_viewings === 1 && cold.totals.recorded_completed_showings === 0,
    'BASELINE_COUNTS_REQUIRED')
    await request(anonymous, foreign + '/view', 'POST', undefined, 405)
    await request(anonymous, listing + '/view', 'POST', undefined, 204, false)
    checks.push('anonymous_owned_view_counted')

    step = 'staff_listing'
    const staffReport = await openListing(staff, 'realtor', path)
    step = 'staff_completed'
    await record(staffReport.page, staffReport.panel, path, 'completed_showing', reportDay)
    checks.push('staff_opens_selected_listing_and_records_showing')
    step = 'staff_feedback'
    await record(staffReport.page, staffReport.panel, path, 'owner_feedback', reportDay)
    checks.push('staff_explicitly_shares_owner_feedback')

    step = 'owner_listing'
    const { page, panel } = await openListing(owner, 'owner', path)
    check(await button(panel, 'Record performance entry').count() === 0, 'OWNER_MUST_BE_READ_ONLY')
    step = 'owner_dates'
    await label(panel, 'Start date').fill(reportDay)
    await label(panel, 'End date').fill(end)
    await observe(page, path, 'GET', () => button(panel, 'Apply dates').click(),
      params => params.get('start') === reportDay && params.get('end') === end)
    const observed = await request(owner, path + query)
    check(observed.listing_id === fixture.performanceListingId && observed.title === title && !observed.can_record
      && observed.range.start === reportDay && observed.range.end === end && observed.range.time_zone === 'America/Santo_Domingo',
    'EXACT_OWNER_WINDOW_REQUIRED')
    checks.push('owner_opens_own_listing_on_mobile_and_applies_dates')

    step = 'mobile_report'
    await text(panel, feedback).waitFor()
    await text(panel, 'Counts cover the tracked dates only. Some dates are missing or still in progress.').waitFor()
    for (const value of ['Confirmed appointments', 'Recorded completed showings', 'Listing approved', 'Santo Domingo time']) {
      await (value === 'Santo Domingo time' ? panel.getByText(value) : text(panel, value)).waitFor()
    }
    await text(panel, 'Daily view counts').filter({ visible: true }).click()
    await panel.getByRole('table', { name: 'Daily view counts', exact: true }).waitFor()
    check(await panel.evaluate(node => navigator.maxTouchPoints > 0
      && node.getBoundingClientRect().right <= window.innerWidth + 1), 'MOBILE_OWNER_REPORT_REQUIRED')
    check(observed.views.lifetime === 10 && observed.views.range_total === 1 && !!observed.views.tracked_from
      && observed.views.coverage === 'partial' && observed.views.days.some(item => item.coverage === 'unavailable' && item.count === null)
      && observed.views.days.some(item => item.coverage === 'partial' && item.count === 1)
      && observed.totals.property_inquiries === 2 && observed.totals.confirmed_viewings === 1 && observed.totals.recorded_completed_showings === 1
      && observed.entries.filter(item => item.kind === 'completed_showing' && item.occurred_on === reportDay).length === 1
      && observed.entries.filter(item => item.kind === 'owner_feedback' && item.text === feedback).length === 1,
    'OBSERVED_COUNTS_COVERAGE_REQUIRED')
    checks.push('coverage_and_real_counts_are_distinct')
    safeProjection(observed)
    safeProjection(await panel.innerText())
    checks.push('owner_report_excludes_private_material', 'foreign_and_unassigned_scope_denied')
    console.log('LISTING_PERFORMANCE_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error('LISTING_PERFORMANCE_BROWSER_FAILURE', step, failureReason(error))
  process.exitCode = 1
})
