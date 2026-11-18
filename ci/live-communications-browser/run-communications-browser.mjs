import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'
import { verifyShareContact } from './verify-share-contact.mjs'

const api = 'http://127.0.0.1:8918'
const landing = 'http://127.0.0.1:8917'
const dashboard = 'http://127.0.0.1:4175'
const fixtureFile = '/browser-fixtures/fixtures.json'
const checks = [
  'actual_public_buyer_seller_renter_entries_en_es',
  'explicit_consent_and_validation_prevent_submission',
  'accepted_receipt_and_next_step_thank_you',
  'property_context_contact_locale_and_idempotent_receipt',
  'actual_signup_verification_locale_and_new_account',
  'admin_summary_queue_detail_accept_customer_email',
  'safe_retry_requires_confirmation_and_cancel_has_no_mutation',
  'uncertain_outcome_and_non_admin_retry_denied',
  'buyer_owner_feeds_are_scoped_and_history_separate',
  'admin_and_owner_manual_share_fields_and_permission',
  'user_initiated_email_whatsapp_composers_canceled_without_send',
  'english_spanish_whole_page_390_and_320_geometry',
  'paused_flags_keep_fallback_and_do_not_replay',
  'zero_external_api_writes_tracking_and_page_errors',
]
const contexts = []
const geometries = []
let stage = 'fixture'
let watchdog
let pageErrors = 0
let externalWrites = 0
let trackingRequests = 0

class CheckFailure extends Error {}
function check(value, code) { if (!value) throw new CheckFailure(code) }
function progress(value) {
  clearTimeout(watchdog)
  stage = value
  console.log('COMMUNICATIONS_BROWSER_STAGE ' + value)
  watchdog = setTimeout(() => {
    console.log('COMMUNICATIONS_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE')
    process.exit(1)
  }, 60000)
}
async function bounded(promise, code, timeout = 10000) {
  let timer
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new CheckFailure(code)), timeout) })]) }
  finally { clearTimeout(timer) }
}
const button = (node, name) => node.getByRole('button', { name, exact: true })
const match = (response, path, method) => new URL(response.url()).origin === api &&
  new URL(response.url()).pathname === path && response.request().method() === method
const json = response => bounded(response.json(), 'RESPONSE_BODY_DEADLINE')
function privateResponse(response) {
  check((response.headers()['cache-control'] || '').split(',').some(x => x.trim() === 'no-store'), 'PRIVATE_RESPONSE_NO_STORE')
}
async function observe(page, path, method, action, status = 200, privateRead = true) {
  const [response] = await bounded(Promise.all([page.waitForResponse(value => match(value, path, method)), action()]), 'ACTION_RESPONSE_DEADLINE', 15000)
  check(response.status() === status, 'ACTUAL_API_STATUS_MISMATCH')
  if (privateRead) privateResponse(response)
  return response
}
async function request(context, path, method = 'GET', data, headers = {}) {
  return context.request.fetch(api + path, { method, data, headers: { Origin: dashboard, ...headers }, maxRedirects: 0, timeout: 8000 })
}
async function session(browser, locale, account) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context)
  context.setDefaultTimeout(10000)
  context.setDefaultNavigationTimeout(15000)
  context.on('page', page => page.on('pageerror', () => { pageErrors += 1 }))
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url())
    if (/google-analytics|googletagmanager|doubleclick|facebook\.com\/tr/.test(url.href)) trackingRequests += 1
    if (![api, landing, dashboard].includes(url.origin)) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method())) externalWrites += 1
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, origins }) => {
    if (!origins.includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    sessionStorage.setItem('lead_modal_dismissed', '1')
  }, { locale, origins: [landing, dashboard] })
  if (account) {
    const response = await request(context, '/auth/login', 'POST', account)
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_AUTH_REQUIRED')
    await response.dispose()
  }
  return context
}
async function geometry(page, label) {
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const value = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth])
    check(value[0] === width && value[1] <= width + 1, 'WHOLE_PAGE_HORIZONTAL_OVERFLOW')
    geometries.push(`${label}_${width}`)
    console.log(`COMMUNICATIONS_BROWSER_GEOMETRY ${label}_${width} ${value.join(' ')}`)
  }
  await page.setViewportSize({ width: 390, height: 844 })
}
async function navigate(page, label) {
  await page.locator('button:has(svg.lucide-menu)').click()
  await button(page.locator('aside'), label).click()
}
function metadata(value) {
  const body = JSON.stringify(value)
  check(!body.includes('@example.com') && !body.includes('private note sentinel') && !/"payload"|"recipient"|"html"/.test(body), 'PRIVATE_PAYLOAD_LEAKED_IN_METADATA')
}
async function getStarted(context, locale, copy, intent) {
  const page = await context.newPage()
  await page.goto(`${landing}/get-started/?intent=${intent}`, { waitUntil: 'domcontentloaded' })
  const form = page.locator('form')
  await form.locator('input[name=name]').fill(`Communications ${intent} ${locale}`)
  await form.locator('input[name=email]').fill(`communications-${intent}-${locale}@example.com`)
  let submissions = 0
  const listener = request => { if (request.url() === api + '/leads' && request.method() === 'POST') submissions += 1 }
  context.on('request', listener)
  await button(form, copy.submit).click()
  check(await form.locator('input[name=consent]').isChecked() === false && submissions === 0, 'CONSENT_MUST_PRECEDE_SUBMISSION')
  await form.locator('input[name=consent]').check()
  const response = await observe(page, '/leads', 'POST', () => button(form, copy.submit).click(), 201)
  const receipt = await json(response), body = response.request().postDataJSON()
  check(body.type === ({ buy: 'buyer_interest', sell: 'seller_interest', rent: 'rental_interest' })[intent] &&
    body.tracking.locale === locale && body.tracking.contact_consent === true && body.tracking.measurement_consent === 'denied' &&
    body.submission_id && receipt.id, 'EXACT_PUBLIC_EVENT_REQUIRED')
  await page.getByRole('heading', { name: copy.success.heading, exact: true }).waitFor()
  check(await page.getByRole('link', { name: intent === 'sell' ? copy.success.selling : copy.success.browse, exact: true }).count() === 1,
    'THANK_YOU_NEXT_STEP_REQUIRED')
  const duplicate = await request(context, '/leads', 'POST', body, { Origin: landing })
  check(duplicate.status() === 201 && (await json(duplicate)).id === receipt.id, 'DUPLICATE_RECEIPT_MUST_MATCH')
  await duplicate.dispose()
  if (intent === 'buy') await geometry(page, `${locale}_thankyou`)
  context.off('request', listener)
  await page.close()
}
async function propertyInquiry(context, locale, copy, fixture) {
  const page = await context.newPage()
  await page.goto(`${landing}/listing/${fixture.listingId}/`, { waitUntil: 'domcontentloaded' })
  await button(page, copy.sidebar.message_agent).click()
  const form = page.locator('form:has(#property-inquiry-name)')
  await form.locator('#property-inquiry-name').fill('')
  await form.locator('#property-inquiry-email').fill('')
  await button(form, copy.sidebar.send).click()
  await form.getByRole('alert').waitFor()
  await form.locator('#property-inquiry-name').fill(`Communications property ${locale}`)
  await form.locator('#property-inquiry-email').fill(`communications-property-${locale}@example.com`)
  await button(form, copy.sidebar.send).click()
  check((await form.getByRole('alert').textContent()).includes(copy.inquiry.errors.consent), 'PROPERTY_CONSENT_ERROR_REQUIRED')
  await form.locator('input[name=contact_consent]').check()
  const response = await observe(page, '/leads', 'POST', () => button(form, copy.sidebar.send).click(), 201)
  const body = response.request().postDataJSON(), receipt = await json(response)
  check(body.property_id === fixture.listingId && body.tracking.locale === locale && body.tracking.contact_consent === true &&
    body.message === undefined && receipt.id, 'PROPERTY_CONTEXT_AND_OPTIONAL_COMMENTS_REQUIRED')
  await page.getByText(copy.sidebar.inquiry_sent, { exact: false }).waitFor()
  await page.getByText(copy.inquiry.follow_up_email, { exact: true }).waitFor()
  const duplicate = await request(context, '/leads', 'POST', body, { Origin: landing })
  check(duplicate.status() === 201 && (await json(duplicate)).id === receipt.id, 'PROPERTY_IDEMPOTENCY_REQUIRED')
  await duplicate.dispose()
  await geometry(page, `${locale}_property`)
  await page.close()
}
async function signup(context, locale, copy, verifyCopy, fixture) {
  const page = await context.newPage()
  await page.goto(`${landing}/signup/`, { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder('Maria Rodríguez', { exact: true }).fill(`Communications signup ${locale}`)
  await page.locator('input[type=tel]').fill('+18095551234')
  await page.locator('input[type=email]').fill(`communications-signup-${locale}@example.com`)
  for (const field of await page.locator('input[type=password]').all()) await field.fill('Synthetic-Rehearsal-4!Password')
  await page.locator('input[type=checkbox]').check()
  await observe(page, '/auth/register', 'POST', () => button(page, copy.signup.form.submit).click(), 201, false)
  await page.waitForURL(url => url.pathname.startsWith('/verify'))
  const control = await request(context, `/__fixture/verification/${locale}`, 'GET', undefined, { 'X-ILDR-Rehearsal': fixture.control })
  check(control.status() === 200, 'SYNTHETIC_VERIFICATION_CODE_REQUIRED')
  const { code } = await json(control)
  check(/^\d{6}$/.test(code), 'ACTUAL_VERIFICATION_CODE_REQUIRED')
  await control.dispose()
  const digits = page.locator('input[inputmode=numeric][maxlength="1"]')
  check(await digits.count() === 6, 'NORMAL_VERIFICATION_ENTRY_REQUIRED')
  for (let index = 0; index < 6; index += 1) await digits.nth(index).fill(code[index])
  const verified = await observe(page, '/auth/verify', 'POST', () => button(page, verifyCopy.verify_btn).click(), 200, false)
  check(verified.request().postDataJSON().locale === locale, 'SIGNUP_LOCALE_REQUIRED')
  const identity = await request(context, '/auth/me')
  const currentUser = await json(identity)
  check(identity.status() === 200 && currentUser.role === 'buyer' &&
    currentUser.email === `communications-signup-${locale}@example.com`, 'ACTUAL_NEW_ACCOUNT_REQUIRED')
  await identity.dispose()
  await page.close()
}
async function administrative(context, locale, copy, fixture) {
  const page = await context.newPage()
  await page.goto(dashboard + '/', { waitUntil: 'domcontentloaded' })
  const summary = await observe(page, '/admin/leads/delivery-summary', 'GET', () => navigate(page, copy.nav['lead-delivery']))
  const summaryBody = await json(summary)
  metadata(summaryBody)
  check(summaryBody.by_step.some(value => value.step === 'customer_email'), 'CUSTOMER_STEP_SUMMARY_REQUIRED')
  await page.locator('#delivery-lead').fill(fixture.trackedLeadId)
  const [filteredQueue, detail] = await bounded(Promise.all([
    page.waitForResponse(response => match(response, '/admin/lead-deliveries', 'GET') &&
      new URL(response.url()).searchParams.get('lead_id') === fixture.trackedLeadId),
    observe(page, `/admin/leads/${fixture.trackedLeadId}/delivery`, 'GET', () => button(page, copy.delivery.queue.apply).click()),
  ]), 'FILTER_AND_DETAIL_RESPONSE_DEADLINE', 15000)
  privateResponse(filteredQueue)
  check(filteredQueue.status() === 200, 'FILTERED_QUEUE_STATUS_REQUIRED')
  const filteredBody = await json(filteredQueue)
  metadata(filteredBody)
  check(filteredBody.items.length === 9 && filteredBody.items.every(job => job.lead_id === fixture.trackedLeadId), 'FILTERED_QUEUE_SCOPE_REQUIRED')
  const detailBody = await json(detail)
  metadata(detailBody)
  check(detailBody.jobs.some(job => job.step === 'customer_email') && detailBody.jobs.some(job => job.outcome_uncertain), 'CUSTOMER_AND_UNCERTAIN_DETAIL_REQUIRED')
  await page.getByText(copy.delivery.steps.customer_email, { exact: true }).first().waitFor()
  let retries = 0
  const listener = request => { if (request.url().endsWith('/retry') && request.method() === 'POST') retries += 1 }
  context.on('request', listener)
  const retryRow = page.locator('tbody tr').filter({ hasText: copy.delivery.steps.email.replace('{{number}}', fixture.failedStep) })
  await button(retryRow, copy.delivery.retry).click()
  await button(page, copy.delivery.cancel).click()
  check(retries === 0, 'CANCEL_MUST_NOT_AUTHORIZE_RETRY')
  await button(retryRow, copy.delivery.retry).click()
  await observe(page, `/admin/lead-deliveries/${fixture.failedJobId}/retry`, 'POST', () => button(page, copy.delivery.confirm.queue).click())
  check(retries === 1, 'ONE_EXPLICIT_RETRY_REQUIRED')
  await page.getByText(copy.delivery.notices.queued, { exact: true }).waitFor()
  context.off('request', listener)
  const repeat = await request(context, `/admin/lead-deliveries/${fixture.failedJobId}/retry`, 'POST', {})
  check(repeat.status() === 409, 'ALREADY_QUEUED_RETRY_MUST_NOT_REPEAT')
  await repeat.dispose()
  const uncertain = await request(context, `/admin/lead-deliveries/${fixture.uncertainJobId}/retry`, 'POST', {})
  check(uncertain.status() === 409, 'UNCERTAIN_OUTCOME_MUST_NOT_REPLAY')
  await uncertain.dispose()
  await geometry(page, `${locale}_delivery`)
  const operations = await observe(page, '/admin/communications', 'GET', () => navigate(page, copy.nav.communications))
  const body = await json(operations)
  metadata(body)
  check(body.items.some(job => job.step === 'customer_email'), 'COMMUNICATIONS_CUSTOMER_STEP_REQUIRED')
  await page.getByText(copy.communications.intro, { exact: true }).waitFor()
  await page.getByText(copy.communications.steps.customer_email, { exact: false }).first().waitFor()
  await geometry(page, `${locale}_communications`)
  await navigate(page, copy.nav.leads)
  await verifyShareContact(page, { id: fixture.shareLeadId, name: 'Browser Share Contact', email: 'browser-share+ci@example.com',
    phone: '+12025550119', message: 'Synthetic private lead note sentinel' }, { language: locale, mobile: true })
  await page.close()
}
async function feeds(browser, locale, copy, fixture) {
  const owner = await session(browser, locale, fixture.accounts.owner)
  const page = await owner.newPage()
  await page.goto(dashboard + '/', { waitUntil: 'domcontentloaded' })
  const response = await observe(page, '/owner/leads', 'GET', () => navigate(page, copy.nav.leads))
  const value = await json(response)
  check(value.items.some(lead => lead.id === fixture.shareLeadId) && !JSON.stringify(value).includes(fixture.foreignLeadId), 'OWNER_SCOPE_REQUIRED')
  const ownerHistory = await observe(page, '/inquiries/for-owner', 'GET', () => button(page, copy.feeds.history_title).click())
  const previousOwner = await json(ownerHistory)
  check(previousOwner.length === 1 && previousOwner[0].message === 'Synthetic historical inquiry sentinel' &&
    !value.items.some(lead => lead.id === previousOwner[0].id), 'OWNER_HISTORY_MUST_REMAIN_SEPARATE')
  await page.getByText('Synthetic historical inquiry sentinel', { exact: true }).waitFor()
  await geometry(page, `${locale}_owner`)
  await button(page, copy.share.open).first().click()
  const dialog = page.getByRole('dialog', { name: copy.share.title, exact: true })
  await dialog.waitFor()
  const choices = dialog.getByRole('checkbox')
  check(await dialog.getByRole('link').count() === 0 && await choices.count() === 4 &&
    await choices.nth(0).isChecked() && !await choices.nth(1).isChecked() &&
    !await choices.nth(2).isChecked() && !await choices.nth(3).isChecked(), 'OWNER_SHARE_PERMISSION_GATE_REQUIRED')
  await button(dialog, copy.share.close).click()
  await page.close()
  const buyer = await session(browser, locale, fixture.accounts.buyer)
  const buyerPage = await buyer.newPage()
  await buyerPage.goto(dashboard + '/', { waitUntil: 'domcontentloaded' })
  const current = await observe(buyerPage, '/leads/mine', 'GET', () => navigate(buyerPage, copy.nav.inquiries))
  const currentBuyer = await json(current)
  check(!JSON.stringify(currentBuyer).includes(fixture.foreignLeadId), 'BUYER_SCOPE_REQUIRED')
  await buyerPage.getByRole('heading', { name: 'Browser communications villa', exact: true }).first().waitFor()
  const buyerHistory = await observe(buyerPage, '/inquiries/mine', 'GET', () => button(buyerPage, copy.feeds.history_title).click())
  const previousBuyer = await json(buyerHistory)
  check(previousBuyer.length === 1 && previousBuyer[0].message === 'Synthetic historical inquiry sentinel' &&
    !currentBuyer.some(lead => lead.id === previousBuyer[0].id), 'BUYER_HISTORY_MUST_REMAIN_SEPARATE')
  await buyerPage.getByText('Synthetic historical inquiry sentinel', { exact: true }).waitFor()
  await geometry(buyerPage, `${locale}_buyer`)
  for (const [path, method, data] of [['/owner/leads', 'GET'], ['/admin/communications', 'GET'],
    [`/admin/lead-deliveries/${fixture.failedJobId}/retry`, 'POST', {}]]) {
    const denied = await request(buyer, path, method, data)
    privateResponse(denied)
    check(denied.status() === 403, 'REAL_NON_ADMIN_BOUNDARY_REQUIRED')
    await denied.dispose()
  }
  await buyerPage.close()
}

let browser
try {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api' &&
    process.env.ILDR_BROWSER_FIXTURES === fixtureFile && (await readdir('/sys/class/net')).join(',') === 'lo', 'HOSTED_LOOPBACK_REQUIRED')
  const info = await lstat(fixtureFile)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixtureFile, 'utf8'))
  check(fixture.scope === 'f01-f03-communications-only' && Object.keys(fixture.accounts).sort().join(',') === 'admin,buyer,owner,realtor', 'EXACT_FIXTURE_REQUIRED')
  check(/^[a-zA-Z0-9_-]{32}$/.test(fixture.control) && Object.entries(fixture.accounts).every(([role, account]) =>
    account.email === `browser-${role}@example.com` && /^[a-zA-Z0-9_-]{32}$/.test(account.password)), 'SYNTHETIC_AUTH_FIXTURE_REQUIRED')
  progress('browser_launch')
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const locale of ['en', 'es']) {
    const copy = JSON.parse(await readFile(`/rehearsal/${locale}.json`, 'utf8'))
    progress(`${locale}_public_forms`)
    const publicContext = await session(browser, locale)
    for (const intent of ['buy', 'sell', 'rent']) await getStarted(publicContext, locale, copy.started, intent)
    const buyer = await session(browser, locale, fixture.accounts.buyer)
    await propertyInquiry(buyer, locale, copy.property, fixture)
    progress(`${locale}_signup`)
    await signup(await session(browser, locale), locale, copy.auth, copy.verify, fixture)
    progress(`${locale}_admin`)
    const admin = await session(browser, locale, fixture.accounts.admin)
    await administrative(admin, locale, copy, { ...fixture,
      failedJobId: locale === 'es' ? fixture.failedEsJobId : fixture.failedJobId,
      failedStep: locale === 'es' ? '3' : '1' })
    progress(`${locale}_feeds`)
    await feeds(browser, locale, copy, fixture)
  }
  progress('paused_fallback')
  const admin = await session(browser, 'en', fixture.accounts.admin)
  const paused = await request(admin, '/__fixture/pause', 'POST', {}, { 'X-ILDR-Rehearsal': fixture.control })
  check(paused.status() === 200 && (await json(paused)).paused === true, 'EXPLICIT_SYNTHETIC_PAUSE_REQUIRED')
  await paused.dispose()
  const fallback = await request(admin, '/leads', 'POST', { type: 'buyer_interest', name: 'Communications fallback', email: 'communications-fallback@example.com' })
  check(fallback.status() === 201, 'FLAGS_OFF_OLD_CLIENT_FALLBACK_REQUIRED')
  await fallback.dispose()
  const flags = await request(admin, '/admin/communications')
  privateResponse(flags)
  const settings = await json(flags)
  check(settings.enabled === false && settings.customer_messages_enabled === false, 'FLAGS_MUST_REMAIN_PAUSED')
  await flags.dispose()
  check(pageErrors === 0 && externalWrites === 0 && trackingRequests === 0 && geometries.length === 24, 'PRIVACY_ERRORS_OR_GEOMETRY_INCOMPLETE')
  progress('browser_teardown')
  for (const context of contexts) await context.close()
  await browser.close()
  clearTimeout(watchdog)
  console.log('COMMUNICATIONS_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks, geometry_checks: geometries.length,
    page_errors: pageErrors, external_writes: externalWrites, tracking_requests: trackingRequests }))
} catch (error) {
  clearTimeout(watchdog)
  const code = error instanceof CheckFailure ? error.message : error?.name === 'TimeoutError' ? 'LOCATOR_OR_EVENT_DEADLINE'
    : /^[A-Z][A-Z0-9_]{1,64}$/.test(error?.message || '') ? error.message : 'BROWSER_OPERATION_FAILED'
  console.log('COMMUNICATIONS_BROWSER_FAILURE ' + stage + ' ' + code)
  await browser?.close().catch(() => {})
  process.exitCode = 1
}
