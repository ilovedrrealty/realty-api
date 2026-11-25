import { lstat, readFile, readdir } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175', LANDING = 'http://127.0.0.1:8917'
const contexts = [], geometryChecks = [], presencePages = new WeakSet()
let browser, timer, stage = 'fixture', pageErrors = 0, assignmentWrites = 0, signupRequests = 0
let externalWrites = 0, blockedExternal = 0, processFailed = false
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
function advance(value) {
  stage = value; clearTimeout(timer)
  timer = setTimeout(() => { console.log('URGENT_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
function processFailure(cause) {
  if (processFailed) return
  processFailed = true
  const known = ['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'TimeoutError', 'TargetClosedError']
  let type = 'other_error'
  try { if (known.includes(cause?.name)) type = cause.name } catch { /* Never emit raw errors. */ }
  try { writeSync(1, 'URGENT_BROWSER_FAILURE ' + stage + ' PROCESS_' + type + '\n') } catch { /* Native exit stays nonzero. */ }
  process.exit(1)
}
process.on('uncaughtException', processFailure)
process.on('unhandledRejection', processFailure)

async function contextFor(locale, account, role) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true,
    hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if (url.origin === API && request.method() === 'PUT' && /\/admin\/users\/[a-f0-9-]+\/assign-realtor$/.test(url.pathname)) assignmentWrites++
    if (url.origin === API && request.method() === 'POST' && url.pathname === '/auth/register') signupRequests++
  })
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url())
    if (![API, DASH, LANDING].includes(url.origin)) {
      blockedExternal++
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, dash, landing }) => {
    if (![dash, landing].includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const native = navigator.onLine
    if (!native) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__ildrUrgentOnline', { value: { before: native, modeled: !native } })
    if (location.origin !== landing || !/^\/signup\/?$/.test(location.pathname)) return
    // Passive observations are installed before load, without a pre-entry round trip.
    const rows = [], allowedTypes = ['insertText', 'insertReplacementText', 'insertFromPaste', 'deleteContentBackward']
    const capture = (phase, event) => {
      if (rows.length >= 64) return
      const input = event?.target || document.querySelector('input[type=tel]')
      if (!(input instanceof HTMLInputElement) || input.type !== 'tel') return
      const selector = document.querySelector('button[aria-label="Country selector"]')
      const country = selector?.getAttribute('data-country') || 'unknown'
      const key = event?.key || 'none', inputType = event?.inputType || 'none'
      rows.push({ phase, digits: input.value.replace(/\D/g, '').slice(0, 20), length: input.value.length,
        start: input.selectionStart ?? -1, end: input.selectionEnd ?? -1,
        country: /^[a-z]{2}$/.test(country) ? country : 'unknown',
        key: /^[0-9+]$/.test(key) || key === 'End' || key === 'none' ? key : 'other',
        input_type: allowedTypes.includes(inputType) || inputType === 'none' ? inputType : 'other',
        connected: input.isConnected })
    }
    const observer = event => capture(event.type, event)
    for (const type of ['keydown', 'input', 'keyup']) document.addEventListener(type, observer, { capture: true, passive: true })
    window.__ildrUrgentPhoneTrace = { rows, finish: () => {
      capture('final', { target: document.querySelector('input[type=tel]') })
      for (const type of ['keydown', 'input', 'keyup']) document.removeEventListener(type, observer, true)
      return rows
    } }
    document.addEventListener('DOMContentLoaded', () => capture('initial'), { once: true, passive: true })
  }, { locale, dash: DASH, landing: LANDING })
  if (account) {
    const reply = await context.request.post(API + '/auth/login', { data: account, headers: { Origin: DASH }, timeout: 15000 })
    check(reply.status() === 200 && (await context.cookies(API)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED')
    await reply.dispose()
    const me = await context.request.get(API + '/auth/me')
    check(me.status() === 200 && (await me.json()).role === role, 'ACTUAL_ROLE_REQUIRED'); await me.dispose()
  }
  return context
}
async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  if (!presencePages.has(page)) {
    const value = await page.evaluate(() => ({ ...window.__ildrUrgentOnline, after: navigator.onLine,
      visible: document.visibilityState === 'visible' }))
    check(Object.keys(value).length === 4 && Object.values(value).every(item => typeof item === 'boolean')
      && value.modeled === !value.before && value.after && value.visible, 'ACTUAL_LOOPBACK_PRESENCE_REQUIRED')
    console.log('URGENT_LOOPBACK_STATE ' + JSON.stringify(value)); presencePages.add(page)
  }
}
async function geometry(page, scope, name) {
  for (const width of [320, 390]) {
    advance(name + '_geometry_' + width)
    await page.setViewportSize({ width, height: 844 }); await foreground(page)
    const measure = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }))
    check(measure.width === width && measure.scroll <= width + 1, 'VIEWPORT_OVERFLOW')
    const controls = await scope.locator('input:not([type=checkbox]), select, button').all()
    for (const control of controls) {
      if (!await control.isVisible()) continue
      const box = await control.boundingBox()
      check(box && box.x >= -1 && box.x + box.width <= width + 1, 'CONTROL_OVERFLOW')
    }
    geometryChecks.push(name + '_' + width)
    console.log('URGENT_BROWSER_GEOMETRY ' + JSON.stringify({ label: name, width, document: measure.scroll }))
  }
}
async function buyerPanel(page, copy, facts) {
  advance('buyer_search_input')
  await page.getByPlaceholder(copy.admin.users_page.search_ph, { exact: true }).fill(facts.buyerEmail)
  advance('buyer_search_result')
  await page.getByText(facts.buyerEmail, { exact: true }).last().click()
  const panel = page.locator('div.fixed.inset-y-0.right-0')
  const section = panel.getByText(copy.admin.agent_choices.assigned_agent, { exact: true }).locator('..')
  advance('buyer_assignment_control')
  await section.locator('select').waitFor()
  advance('buyer_agent_choices')
  await section.locator(`option[value="${facts.agentId}"]`).waitFor({ state: 'attached' })
  return { panel, section, select: section.locator('select') }
}
async function openUsers(context, copy, facts) {
  const page = await context.newPage()
  advance('admin_document')
  await page.goto(DASH + '/?workspace=admin', { waitUntil: 'domcontentloaded' }); await foreground(page)
  advance('admin_menu')
  await page.locator('button:has(svg.lucide-menu)').click()
  advance('admin_users_navigation')
  await button(page.locator('aside'), copy.nav.users).click()
  return { page, ...await buyerPanel(page, copy, facts) }
}
async function assign(page, section, value, copy, facts) {
  await foreground(page)
  advance(value ? 'buyer_assign_select' : 'buyer_clear_select')
  await section.locator('select').selectOption(value)
  advance(value ? 'buyer_assign_action' : 'buyer_clear_action')
  const [response] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).origin === API
      && new URL(response.url()).pathname === `/admin/users/${facts.buyerId}/assign-realtor` && response.request().method() === 'PUT'),
    button(section, copy.admin.agent_choices.assign).click(),
  ])
  check(response.status() === 204 && response.request().postDataJSON().realtor_id === (value || null), 'ACTUAL_ASSIGNMENT_ACTION_REQUIRED')
}
async function ownAgent(context, expected) {
  const response = await context.request.get(API + '/auth/me/agent', { timeout: 15000 })
  check(response.status() === 200 && (await response.json()).realtor_id === expected, 'OWN_AGENT_READBACK_REQUIRED')
  await response.dispose()
  const me = await context.request.get(API + '/auth/me', { timeout: 15000 })
  check(me.status() === 200 && (await me.json()).role === 'buyer', 'BUYER_ROLE_PRESERVED_REQUIRED'); await me.dispose()
}
async function selectPhoneCountry(form, country) {
  await form.getByRole('combobox', { name: 'Country selector', exact: true }).click()
  await form.locator(`li[role="option"][data-country="${country}"]`).click()
  await form.locator(`button[aria-label="Country selector"][data-country="${country}"]`).waitFor()
}
async function phoneJourney(copy, locale) {
  advance('signup_' + locale + '_document')
  const context = await contextFor(locale), page = await context.newPage()
  await page.goto(LANDING + '/signup/', { waitUntil: 'domcontentloaded' }); await foreground(page)
  const form = page.getByRole('heading', { name: copy.auth.signup.form.heading, exact: true }).locator('..')
  await form.getByRole('button', { name: copy.auth.signup.form.submit, exact: true }).waitFor()
  const phone = form.locator('input[type=tel]'), full = '+5491123456789'
  advance('signup_' + locale + '_country')
  await selectPhoneCountry(form, 'ar')
  advance('signup_' + locale + '_typing')
  await phone.fill('+54'); await phone.press('End'); await phone.pressSequentially('91123456789')
  const trace = await page.evaluate(() => {
    const rows = window.__ildrUrgentPhoneTrace.finish(); delete window.__ildrUrgentPhoneTrace; return rows
  })
  console.log('URGENT_SYNTHETIC_PHONE_TRACE ' + JSON.stringify({ locale, rows: trace }))
  check((await phone.inputValue()).replace(/\D/g, '') === full.slice(1), 'ARGENTINA_FINAL_TYPED_DIGIT_REQUIRED')
  advance('signup_' + locale + '_paste_from_do')
  await selectPhoneCountry(form, 'do')
  await phone.fill(full)
  await form.locator('button[aria-label="Country selector"][data-country="ar"]').waitFor()
  check((await phone.inputValue()).replace(/\D/g, '') === full.slice(1), 'ARGENTINA_PASTE_REQUIRED')
  for (const number of ['+18095551234', '+12125551234']) {
    await phone.fill(number)
    check((await phone.inputValue()).replace(/\D/g, '') === number.slice(1), 'DR_US_COMPLETE_PHONE_REQUIRED')
  }
  advance('signup_' + locale + '_invalid')
  await form.getByPlaceholder('Maria Rodríguez', { exact: true }).fill('Synthetic Signup')
  await form.locator('input[type=email]').fill('synthetic-signup@example.invalid')
  const passwords = form.locator('input[type=password]')
  await passwords.nth(0).fill('Synthetic-Only!234A'); await passwords.nth(1).fill('Synthetic-Only!234A')
  await form.locator('input[type=checkbox]').check()
  await phone.fill('+5491')
  await button(form, copy.auth.signup.form.submit).click()
  await form.getByText(copy.auth.signup.validation.phone_invalid, { exact: true }).waitFor()
  check(signupRequests === 0, 'INVALID_PHONE_NO_SIGNUP_REQUEST_REQUIRED')
  await phone.fill(full)
  await form.getByText(copy.auth.signup.validation.phone_invalid, { exact: true }).waitFor({ state: 'hidden' })
  await geometry(page, form, locale + '_signup')
}

try {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true', 'HOSTED_LINUX_REQUIRED')
  check(JSON.stringify(await readdir('/sys/class/net')) === '["lo"]', 'LOOPBACK_NETWORK_REQUIRED')
  const info = await lstat(process.env.ILDR_BROWSER_FIXTURES)
  check(info.isFile() && !info.isSymbolicLink() && (info.mode & 0o777) === 0o600 && info.size <= 16384, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(process.env.ILDR_BROWSER_FIXTURES, 'utf8'))
  check(fixture.scope === 'buyer-phone-only', 'EXACT_SCOPE_REQUIRED')
  const facts = fixture.facts
  const en = JSON.parse(await readFile('/browser-run/en.json', 'utf8')), es = JSON.parse(await readFile('/browser-run/es.json', 'utf8'))
  browser = await chromium.launch({ headless: true })
  const adminEn = await contextFor('en', fixture.accounts.admin, 'admin')
  const buyer = await contextFor('en', fixture.accounts.buyer, 'buyer')
  advance('buyer_en_assignment')
  const english = await openUsers(adminEn, en, facts)
  check(await english.select.inputValue() === '', 'BUYER_STARTS_UNASSIGNED_REQUIRED')
  await assign(english.page, english.section, facts.agentId, en, facts)
  await ownAgent(buyer, facts.agentId)
  advance('buyer_en_reload')
  await english.page.reload({ waitUntil: 'domcontentloaded' }); await foreground(english.page)
  const persisted = await buyerPanel(english.page, en, facts)
  check(await persisted.select.inputValue() === facts.agentId, 'ADMIN_PERSISTED_ASSIGNMENT_REQUIRED')
  await geometry(english.page, persisted.section, 'en_buyer_assignment')
  const adminEs = await contextFor('es', fixture.accounts.admin, 'admin')
  advance('buyer_es_clear')
  const spanish = await openUsers(adminEs, es, facts)
  check(await spanish.select.inputValue() === facts.agentId, 'SPANISH_ASSIGNED_BUYER_REQUIRED')
  await assign(spanish.page, spanish.section, '', es, facts)
  await ownAgent(buyer, null)
  await spanish.page.reload({ waitUntil: 'domcontentloaded' }); await foreground(spanish.page)
  const cleared = await buyerPanel(spanish.page, es, facts)
  check(await cleared.select.inputValue() === '', 'ADMIN_PERSISTED_CLEAR_REQUIRED')
  await geometry(spanish.page, cleared.section, 'es_buyer_assignment')
  await phoneJourney(en, 'en'); await phoneJourney(es, 'es')
  check(geometryChecks.length === 8 && new Set(geometryChecks).size === 8 && pageErrors === 0
    && assignmentWrites === 2 && signupRequests === 0 && externalWrites === 0, 'EXACT_BROWSER_SCOPE_REQUIRED')
  clearTimeout(timer)
  console.log('URGENT_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', geometry: 8, locales: ['en', 'es'],
    assignment_writes: 2, signup_requests: 0, page_errors: 0, external_writes: 0, blocked_external_requests: blockedExternal }))
} catch (cause) {
  let reason = 'OTHER_DRIVER_ERROR'
  try {
    if (cause instanceof Failure) reason = cause.message
    else if (cause?.name === 'TimeoutError') reason = 'LOCATOR_OR_EVENT_DEADLINE'
    else if (typeof cause?.message === 'string' && cause.message.includes('strict mode violation')) reason = 'STRICT_LOCATOR'
  } catch { /* Only finite source-owned classifications are emitted. */ }
  console.log('URGENT_BROWSER_FAILURE ' + stage + ' ' + reason); process.exitCode = 1
} finally {
  clearTimeout(timer)
  await Promise.allSettled(contexts.map(context => context.close()))
  if (browser) await browser.close()
}
