import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918'
const dashboard = 'http://127.0.0.1:4175'
const fixtureFile = '/browser-fixtures/fixtures.json'
const ownPath = '/auth/me/delete-request'
const queuePath = '/admin/account-deletion-requests'
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/
const checks = [
  'normal_member_and_admin_settings_entrypoints',
  'typed_email_and_explicit_request_confirmation',
  'durable_request_status_after_reload',
  'admin_review_and_hold_reason_visible_to_member',
  'stale_member_cancel_requires_current_state',
  'current_member_cancel_and_replay_are_durable',
  'stale_admin_review_cannot_override_cancellation',
  'non_admin_direct_queue_and_review_denied',
  'all_four_roles_keep_active_accounts',
  'english_spanish_mobile_labels_fit',
  'admin_own_settings_request_and_cancel',
]
const contexts = []
const geometries = []
let stage = 'fixture'
let watchdog
let failed = false

class CheckFailure extends Error {
  constructor(code, status) {
    super(code)
    this.status = status
  }
}

function check(condition, code, status) {
  if (!condition) throw new CheckFailure(code, status)
}

function failure(error) {
  if (failed) return
  failed = true
  process.exitCode = 1
  let code = 'BROWSER_OPERATION_FAILED'
  if (error instanceof CheckFailure) code = error.message
  else if (error?.name === 'TimeoutError') code = 'LOCATOR_OR_EVENT_DEADLINE'
  else if (String(error?.message).includes('strict mode violation')) code = 'LOCATOR_AMBIGUOUS'
  const status = Number.isInteger(error?.status) ? ` status=${error.status}` : ''
  console.log(`F09_BROWSER_FAILURE ${stage} ${code}${status}`)
}

function progress(next) {
  clearTimeout(watchdog)
  stage = next
  console.log('F09_BROWSER_STAGE ' + stage)
  watchdog = setTimeout(() => {
    failure(new CheckFailure('STAGE_WATCHDOG_DEADLINE'))
    setTimeout(() => process.exit(1), 250)
  }, 25000)
}

async function bounded(promise, code, milliseconds = 8000) {
  let timer
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new CheckFailure(code)), milliseconds)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

function privateResponse(response) {
  check((response.headers()['cache-control'] ?? '').split(',').some(value => value.trim() === 'no-store'),
    'PRIVATE_STATE_MUST_BE_NO_STORE')
}

async function json(response) {
  return bounded(response.json(), 'RESPONSE_JSON_DEADLINE')
}

function validState(state, expectedStatus, version) {
  check(Object.keys(state).sort().join(',') === 'request_id,requested_at,review_after,review_reason,reviewed_at,status,version',
    'EXACT_PRIVATE_STATE_SCHEMA_REQUIRED')
  check(state.status === expectedStatus && state.review_after === null && state.version === version,
    'PERSISTED_STATE_MISMATCH')
  if (expectedStatus === 'none') {
    check(state.request_id === null && state.requested_at === null, 'EMPTY_STATE_REQUIRED')
  } else {
    check(uuid.test(state.request_id) && Number.isFinite(Date.parse(state.requested_at)), 'DURABLE_REQUEST_ID_REQUIRED')
  }
  return state
}

function isResponse(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function observe(page, path, method, act, expected = 200) {
  const [response] = await bounded(Promise.all([
    page.waitForResponse(value => isResponse(value, path, method)),
    act(),
  ]), 'ACTION_RESPONSE_DEADLINE', 15000)
  privateResponse(response)
  check(response.status() === expected, 'ACTION_STATUS_MISMATCH', response.status())
  return response
}

async function request(context, path, method = 'GET', data) {
  return context.request.fetch(api + path, {
    method, data, maxRedirects: 0, timeout: 8000, headers: { Origin: dashboard },
  })
}

async function apiState(context) {
  const response = await request(context, ownPath)
  try {
    privateResponse(response)
    check(response.status() === 200, 'OWN_STATE_STATUS_MISMATCH', response.status())
    return await json(response)
  } finally {
    await response.dispose()
  }
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true' &&
    process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api' &&
    process.env.ILDR_BROWSER_FIXTURES === fixtureFile, 'HOSTED_FIXTURE_REQUIRED')
  check((await readdir('/sys/class/net')).join(',') === 'lo', 'LOOPBACK_ONLY_REQUIRED')
  const info = await lstat(fixtureFile)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size <= 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(fixtureFile, 'utf8'))
  check(fixture.scope === 'account-deletion-review-only' &&
    Object.keys(fixture.accounts).sort().join(',') === 'admin,buyer,owner,realtor', 'EXACT_FOUR_ACCOUNTS_REQUIRED')
  for (const [role, account] of Object.entries(fixture.accounts)) {
    check(account.email === `browser-${role}@example.com` && /^[a-zA-Z0-9_-]{32}$/.test(account.password),
      'SYNTHETIC_CREDENTIAL_SCHEMA_REQUIRED')
  }
  return fixture
}

async function session(browser, account, language, mobile = true) {
  const context = await browser.newContext({
    viewport: mobile ? { width: 390, height: 844 } : { width: 1366, height: 900 },
    hasTouch: mobile, isMobile: mobile, serviceWorkers: 'block',
  })
  contexts.push(context)
  context.setDefaultTimeout(10000)
  context.setDefaultNavigationTimeout(10000)
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    return [api, dashboard].includes(url.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(({ locale, origin }) => {
    if (window.location.origin !== origin) return
    localStorage.setItem('ildr_lang', locale)
    sessionStorage.setItem('lead_modal_dismissed', '1')
  }, { locale: language, origin: dashboard })
  const response = await request(context, '/auth/login', 'POST', account)
  try {
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_AUTH_SESSION_REQUIRED', response.status())
  } finally {
    await response.dispose()
  }
  return context
}

const control = (node, role, label) => node.getByRole(role, { name: label, exact: true })
const button = (node, label) => control(node, 'button', label)

async function navigate(page, label) {
  if (page.viewportSize().width < 768) {
    await page.locator('button:has(svg.lucide-menu)').click()
  }
  await button(page.locator('aside'), label).click()
}

async function identityPage(context, role) {
  const page = await context.newPage()
  const [response] = await Promise.all([
    page.waitForResponse(value => isResponse(value, '/auth/me')),
    page.goto(dashboard + '/', { waitUntil: 'domcontentloaded' }),
  ])
  const identity = await json(response)
  check(response.status() === 200 && identity.role === role, 'ACTUAL_ROLE_IDENTITY_REQUIRED')
  return page
}

async function settings(page, language, copy, admin = false) {
  await navigate(page, language === 'en' ? 'Settings' : 'Configuración')
  return security(page, language, copy, admin)
}

async function security(page, language, copy, admin = false) {
  const response = await observe(page, ownPath, 'GET', () =>
    button(page, admin || language === 'en' ? 'Security' : 'Seguridad').click())
  const section = control(page, 'region', copy.own.title)
  await section.waitFor()
  return { section, state: await json(response) }
}

async function geometry(page, region, label, width) {
  await page.setViewportSize({ width, height: 844 })
  const values = await region.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return [innerWidth, document.documentElement.scrollWidth, Math.floor(bounds.left),
      Math.ceil(bounds.right), element.scrollWidth, element.clientWidth]
  })
  console.log('F09_BROWSER_GEOMETRY ' + label + ' ' + values.join(' '))
  const [viewport, documentWidth, left, right, scroll, client] = values
  geometries.push(documentWidth <= viewport + 1 && left >= -1 && right <= viewport + 1 && scroll <= client + 1)
}

async function submitRequest(page, section, copy, email) {
  const field = section.getByLabel(copy.own.email_label, { exact: true })
  const confirm = control(section, 'checkbox', copy.own.confirm)
  check(await field.getAttribute('type') === 'email' && await field.getAttribute('required') !== null &&
    !await confirm.isChecked(), 'LABELLED_UNCHECKED_FORM_REQUIRED')
  await field.fill('other@example.com')
  await confirm.check()
  check(await button(section, copy.own.request).isDisabled(), 'MISMATCHED_EMAIL_MUST_BLOCK_REQUEST')
  await field.fill(email)
  await confirm.uncheck()
  check(await button(section, copy.own.request).isDisabled(), 'EXPLICIT_CONFIRMATION_REQUIRED')
  await confirm.check()
  const response = await observe(page, ownPath, 'POST', () => button(section, copy.own.request).click())
  const payload = response.request().postDataJSON()
  check(Object.keys(payload).sort().join(',') === 'confirm_email,confirm_request' &&
    payload.confirm_email === email && payload.confirm_request === true, 'EXPLICIT_JSON_REQUEST_REQUIRED')
  return validState(await json(response), 'pending_review', 1)
}

async function openQueue(page, language, copy) {
  await observe(page, queuePath, 'GET', () => navigate(page,
    language === 'en' ? 'Account deletion review' : 'Revisión de eliminación de cuentas'))
  const queue = control(page, 'region', copy.admin.title)
  await observe(page, queuePath, 'GET', () => queue.getByLabel(copy.admin.filter, { exact: true }).selectOption('active'))
  return queue
}

async function chooseReview(page, queue, copy, email, status, reason) {
  const row = queue.locator('li').filter({ has: page.getByText(email, { exact: true }) })
  await button(row, copy.admin.review).click()
  const form = control(queue, 'form', copy.admin.review)
  await form.getByText(copy.admin.reason_visible, { exact: true }).waitFor()
  check(await button(form, copy.admin.save).isDisabled(), 'REVIEW_REASON_REQUIRED')
  await form.getByLabel(copy.admin.decision, { exact: true }).selectOption(status)
  await form.getByLabel(copy.admin.reason, { exact: true }).fill(reason)
  return form
}

async function saveReview(page, form, copy, original, status, reason, expectedVersion, email) {
  const response = await observe(page, `${queuePath}/${original.request_id}/review`, 'POST', () => button(form, copy.admin.save).click())
  const payload = response.request().postDataJSON()
  check(payload.expected_version === expectedVersion && payload.status === status && payload.reason === reason,
    'VERSIONED_REVIEW_JSON_REQUIRED')
  const state = validState(await json(response), status, expectedVersion + 1)
  check(state.request_id === original.request_id && state.review_reason === reason && state.reviewed_at !== null,
    'EXACT_REVIEW_NOTE_REQUIRED')
  const row = page.locator('li').filter({ has: page.getByText(email, { exact: true }) })
  await row.getByText(copy.statuses[status], { exact: true }).waitFor()
  await row.getByText(reason, { exact: false }).waitFor()
  return state
}

async function denied(context, path, method = 'GET', data, expected = 403) {
  const response = await request(context, path, method, data)
  try {
    privateResponse(response)
    check(response.status() === expected, 'SCOPED_DENIAL_REQUIRED', response.status())
  } finally {
    await response.dispose()
  }
}

async function journey(browser, fixture, language, role, copy) {
  progress(`${language}_member_entry`)
  const member = await session(browser, fixture.accounts[role], language)
  const page = await identityPage(member, role)
  const opened = await settings(page, language, copy)
  validState(opened.state, 'none', 0)
  await geometry(page, opened.section, `${language}_member390`, 390)

  progress(`${language}_request`)
  const original = await submitRequest(page, opened.section, copy, fixture.accounts[role].email)
  progress(`${language}_reload`)
  await page.reload({ waitUntil: 'domcontentloaded' })
  const persisted = await security(page, language, copy)
  check(persisted.state.request_id === original.request_id && persisted.state.requested_at === original.requested_at,
    'REQUEST_MUST_SURVIVE_PAGE_RELOAD')
  validState(persisted.state, 'pending_review', 1)

  progress(`${language}_admin_entry`)
  const admin = await session(browser, fixture.accounts.admin, language, false)
  const adminPage = await identityPage(admin, 'admin')
  const queue = await openQueue(adminPage, language, copy)

  progress(`${language}_review`)
  const reviewReason = `Synthetic F09 review ${language}`
  const reviewForm = await chooseReview(adminPage, queue, copy, fixture.accounts[role].email, 'reviewed', reviewReason)
  await saveReview(adminPage, reviewForm, copy, original, 'reviewed', reviewReason, 1, fixture.accounts[role].email)

  progress(`${language}_hold`)
  const holdReason = `Synthetic F09 hold ${language}`
  const holdForm = await chooseReview(adminPage, queue, copy, fixture.accounts[role].email, 'retention_hold', holdReason)
  await saveReview(adminPage, holdForm, copy, original, 'retention_hold', holdReason, 2, fixture.accounts[role].email)
  await geometry(adminPage, queue, `${language}_queue320`, 320)

  progress(`${language}_stale_cancel`)
  await observe(page, ownPath + '/cancel', 'POST', () => button(persisted.section, copy.own.cancel).click(), 409)
  await persisted.section.getByText(copy.stale, { exact: true }).waitFor()
  check(await button(persisted.section, copy.own.cancel).isDisabled(), 'STALE_CANCEL_MUST_WAIT_FOR_REFRESH')
  const freshResponse = await observe(page, ownPath, 'GET', () => button(persisted.section, copy.refresh).click())
  validState(await json(freshResponse), 'retention_hold', 3)
  await persisted.section.getByText(holdReason, { exact: true }).waitFor()
  await geometry(page, persisted.section, `${language}_member320`, 320)

  const staleForm = await chooseReview(adminPage, queue, copy, fixture.accounts[role].email, 'reviewed', `Synthetic F09 stale ${language}`)
  progress(`${language}_cancel`)
  const cancelledResponse = await observe(page, ownPath + '/cancel', 'POST', () => button(persisted.section, copy.own.cancel).click())
  const cancelled = validState(await json(cancelledResponse), 'cancelled', 4)
  check(cancelled.request_id === original.request_id && cancelled.requested_at === original.requested_at,
    'CANCELLATION_MUST_TARGET_CURRENT_REQUEST')
  const replay = await request(member, ownPath + '/cancel', 'POST', { request_id: original.request_id, expected_version: 3 })
  try {
    privateResponse(replay)
    validState(await json(replay), 'cancelled', 4)
    check(replay.status() === 200, 'CANCELLATION_REPLAY_MUST_BE_IDEMPOTENT')
  } finally {
    await replay.dispose()
  }
  await page.reload({ waitUntil: 'domcontentloaded' })
  validState((await security(page, language, copy)).state, 'cancelled', 4)

  progress(`${language}_stale_review`)
  await observe(adminPage, `${queuePath}/${original.request_id}/review`, 'POST', () => button(staleForm, copy.admin.save).click(), 409)
  await queue.getByText(copy.stale, { exact: true }).waitFor()
  check(await button(staleForm, copy.admin.save).isDisabled(), 'CANCELLED_REVIEW_MUST_WAIT_FOR_REFRESH')
  await observe(adminPage, queuePath, 'GET', () => button(queue, copy.refresh).click())
  await observe(adminPage, queuePath, 'GET', () => queue.getByLabel(copy.admin.filter, { exact: true }).selectOption('cancelled'))
  const cancelledRow = queue.locator('li').filter({ has: adminPage.getByText(fixture.accounts[role].email, { exact: true }) })
  await cancelledRow.getByText(copy.statuses.cancelled, { exact: true }).waitFor()
  check(await button(cancelledRow, copy.admin.review).count() === 0, 'CANCELLED_REVIEW_CONTROL_MUST_BE_ABSENT')

  progress(`${language}_denials`)
  const [nonAdminIdentity] = await Promise.all([
    page.waitForResponse(response => isResponse(response, '/auth/me')),
    page.goto(dashboard + '/account-deletion?workspace=admin', { waitUntil: 'domcontentloaded' }),
  ])
  check(nonAdminIdentity.status() === 200 && (await json(nonAdminIdentity)).role === role,
    'DIRECT_ROUTE_MUST_KEEP_REAL_MEMBER_ROLE')
  await page.locator('aside').waitFor({ state: 'attached' })
  check(await control(page, 'region', copy.admin.title).count() === 0, 'NON_ADMIN_DIRECT_UI_QUEUE_MUST_BE_ABSENT')
  await denied(member, queuePath)
  await denied(member, `${queuePath}/${original.request_id}/review`, 'POST', {
    expected_version: 4, status: 'reviewed', reason: reviewReason,
  })

  progress(`${language}_admin_own`)
  const own = await settings(adminPage, language, copy, true)
  check(['none', 'cancelled'].includes(own.state.status), 'ADMIN_OWN_PRECONDITION_REQUIRED')
  await geometry(adminPage, own.section, `${language}_admin320`, 320)
  const ownRequest = await submitRequest(adminPage, own.section, copy, fixture.accounts.admin.email)
  const ownCancel = await observe(adminPage, ownPath + '/cancel', 'POST', () => button(own.section, copy.own.cancel).click())
  const ownSaved = validState(await json(ownCancel), 'cancelled', 2)
  check(ownSaved.request_id === ownRequest.request_id, 'ADMIN_OWN_CANCELLATION_ID_REQUIRED')
  validState(await apiState(admin), 'cancelled', 2)
}

let browser
try {
  progress('fixture')
  const fixture = await readFixture()
  const locales = {}
  for (const language of ['en', 'es']) {
    locales[language] = JSON.parse(await readFile(new URL(`./${language}.json`, import.meta.url), 'utf8'))
  }
  check(locales.en.own.title !== locales.es.own.title, 'DISTINCT_BILINGUAL_LABELS_REQUIRED')
  progress('browser_launch')
  browser = await chromium.launch({ headless: true })
  await journey(browser, fixture, 'en', 'owner', locales.en)
  await journey(browser, fixture, 'es', 'buyer', locales.es)
  progress('realtor_entry')
  const realtor = await session(browser, fixture.accounts.realtor, 'es')
  const realtorPage = await identityPage(realtor, 'realtor')
  const realtorOwn = await settings(realtorPage, 'es', locales.es)
  validState(realtorOwn.state, 'none', 0)
  await geometry(realtorPage, realtorOwn.section, 'es_realtor320', 320)
  progress('realtor_denial')
  await denied(realtor, queuePath)
  progress('mobile_geometry')
  check(geometries.length === 9 && geometries.every(Boolean), 'MOBILE_HORIZONTAL_OVERFLOW')
  progress('browser_teardown')
} catch (error) {
  failure(error)
} finally {
  clearTimeout(watchdog)
  for (const context of contexts) {
    try {
      await bounded(context.close(), 'CONTEXT_CLOSE_DEADLINE')
    } catch (error) {
      failure(error)
    }
  }
  if (browser) {
    try {
      await bounded(browser.close(), 'BROWSER_CLOSE_DEADLINE')
    } catch (error) {
      failure(error)
    }
  }
}
if (!failed) console.log('F09_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks }))
