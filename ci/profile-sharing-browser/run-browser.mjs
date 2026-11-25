import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175', LANDING = 'http://127.0.0.1:8917'
const APP_CANONICAL = 'https://app.ilovedrrealty.com', SITE_CANONICAL = 'https://ilovedrrealty.com'
const contexts = [], geometryChecks = [], journeys = [], presencePages = new WeakSet()
let browser, facts, accounts, stage = 'fixture', timer, processFailed = false
let pageErrors = 0, externalWrites = 0, unexpectedMutations = 0, trackingRequests = 0, blockedExternal = 0
let linkChecks = 0, nativeCalls = 0, presenceCount = 0
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const escape = value => [...value].map(character => {
  const slash = String.fromCharCode(92)
  return '*+?^${}()|[]'.includes(character) || character === slash ? slash + character : character
}).join('')
function advance(value) {
  check(/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(value), 'FINITE_STAGE_REQUIRED')
  stage = value; clearTimeout(timer)
  timer = setTimeout(() => { console.log('SHARING_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
function processFailure(cause) {
  if (processFailed) return
  processFailed = true
  const known = ['Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'TimeoutError', 'TargetClosedError']
  let type = 'other_error'
  try { if (known.includes(cause?.name)) type = cause.name } catch { /* No raw error data. */ }
  try { writeSync(1, 'SHARING_BROWSER_FAILURE ' + stage + ' PROCESS_' + type + '\n') } catch { /* Native exit stays nonzero. */ }
  process.exit(1)
}
process.on('uncaughtException', processFailure)
process.on('unhandledRejection', processFailure)

async function read(context, path, expected = 200) {
  check(path.startsWith('/') && !path.startsWith('//') && !path.includes('#'), 'OWNED_API_PATH_REQUIRED')
  const reply = await context.request.get(API + path, { timeout: 15000, maxRedirects: 0 })
  try { check(reply.status() === expected, 'ACTUAL_API_STATUS_REQUIRED'); return await reply.json() }
  finally { await reply.dispose() }
}
async function contextFor(locale, actor) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true,
    hasTouch: true, serviceWorkers: 'block' })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  for (const origin of [DASH, LANDING]) await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin })
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if (url.origin === API && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
      && !(request.method() === 'POST' && url.pathname === '/auth/login')) unexpectedMutations++
    if (/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href)) trackingRequests++
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
  await context.addInitScript(({ locale, owned }) => {
    if (!owned.includes(location.origin)) return
    localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const before = navigator.onLine
    if (!before) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__sharingOnline', { value: { before, modeled: !before } })
    // A deliberate, recorded CI capture validates the actual product payload.
    // It never opens an operating-system sheet or claims recipient delivery.
    const harness = { calls: [], abortNext: false }
    Object.defineProperty(window, '__sharingHarness', { value: harness })
    Object.defineProperty(navigator, 'share', { configurable: true, value: async payload => {
      harness.calls.push(JSON.parse(JSON.stringify(payload)))
      if (harness.abortNext) { harness.abortNext = false; const error = new Error('Synthetic cancellation'); error.name = 'AbortError'; throw error }
    } })
  }, { locale, owned: [DASH, LANDING] })
  if (actor) {
    const reply = await context.request.post(API + '/auth/login', { data: accounts[actor],
      headers: { Origin: DASH }, timeout: 15000, maxRedirects: 0 })
    try { check(reply.status() === 200 && (await context.cookies(API)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED') }
    finally { await reply.dispose() }
    check((await read(context, '/auth/me')).role === (actor === 'admin' ? 'admin' : 'buyer'), 'ACTUAL_ROLE_REQUIRED')
  }
  return context
}
async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
  if (!presencePages.has(page)) {
    const value = await page.evaluate(() => ({ ...window.__sharingOnline, after: navigator.onLine,
      visible: document.visibilityState === 'visible' }))
    check(Object.keys(value).length === 4 && Object.values(value).every(item => typeof item === 'boolean')
      && value.modeled === !value.before && value.after && value.visible, 'ACTUAL_LOOPBACK_PRESENCE_REQUIRED')
    console.log('SHARING_LOOPBACK_STATE ' + JSON.stringify(value)); presencePages.add(page); presenceCount++
  }
}
async function geometry(page, scope, label) {
  for (const width of [320, 390]) {
    advance(label + '_geometry_' + width)
    await page.setViewportSize({ width, height: 844 }); await foreground(page)
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(value.width === width && value.document <= width + 1, 'VIEWPORT_OVERFLOW')
    for (const control of await scope.locator('input, select, button, a, summary').all()) {
      if (!await control.isVisible()) continue
      const box = await control.boundingBox()
      check(box && box.x >= -1 && box.x + box.width <= width + 1, 'CONTROL_OVERFLOW')
    }
    const row = { label, ...value }; geometryChecks.push(row)
    console.log('SHARING_BROWSER_GEOMETRY ' + JSON.stringify(row))
  }
}
async function navigate(page, path) {
  check(path.startsWith('/') && !path.startsWith('//'), 'FIXED_DASHBOARD_PATH_REQUIRED')
  await page.goto(DASH + path, { waitUntil: 'domcontentloaded' }); await foreground(page)
}
function profileLabels(copy, kind = 'profile') {
  return { open: copy.share[kind === 'contact' ? 'link_contact_open' : 'link_profile_open'],
    title: copy.share[kind === 'contact' ? 'link_contact_title' : 'link_profile_title'],
    copy: copy.share.link_copy, native: copy.share.link_native, copied: copy.share.link_copied,
    email: copy.share.compose_email, whatsapp: copy.share.compose_whatsapp, canceled: copy.share.link_canceled }
}
async function native(page, scope, name, expected, abort = false) {
  const before = await page.evaluate(abort => { window.__sharingHarness.abortNext = abort; return window.__sharingHarness.calls.length }, abort)
  await button(scope, name).click()
  await page.waitForFunction(count => window.__sharingHarness.calls.length === count + 1, before)
  const actual = await page.evaluate(count => window.__sharingHarness.calls[count], before)
  check(JSON.stringify(actual) === JSON.stringify(expected), 'EXACT_NATIVE_PAYLOAD_REQUIRED'); nativeCalls++
}
async function composers(scope, labels, preview, subject) {
  const email = new URL(await scope.getByRole('link', { name: labels.email, exact: true }).getAttribute('href'))
  const whatsapp = new URL(await scope.getByRole('link', { name: labels.whatsapp, exact: true }).getAttribute('href'))
  check(email.protocol === 'mailto:' && email.pathname === '' && email.searchParams.get('subject') === subject
    && email.searchParams.get('body') === preview && [...email.searchParams.keys()].join(',') === 'subject,body', 'EXACT_RECIPIENT_FREE_EMAIL_REQUIRED')
  check(whatsapp.origin === 'https://wa.me' && whatsapp.pathname === '/' && whatsapp.searchParams.get('text') === preview
    && [...whatsapp.searchParams.keys()].join(',') === 'text', 'EXACT_RECIPIENT_FREE_WHATSAPP_REQUIRED')
}
async function sharedLink(page, scope, labels, expectedURL, cancel = false) {
  const summary = scope.locator('summary').filter({ hasText: new RegExp('^' + escape(labels.open) + '$') }).first()
  await summary.click()
  const panel = summary.locator('..'), preview = labels.title + '\n' + expectedURL
  check(await panel.locator('pre').innerText() === preview, 'GENERIC_LINK_PREVIEW_REQUIRED')
  check(!preview.includes(facts.privateNote) && !preview.includes(facts.contactEmail)
    && !preview.includes('NEVER_SHARE_UNPUBLISHED_DRAFT_NAME'), 'NO_PRIVATE_LINK_METADATA_REQUIRED')
  await composers(panel, labels, preview, labels.title)
  await button(panel, labels.copy).click(); await panel.getByText(labels.copied, { exact: true }).waitFor()
  check(await page.evaluate(() => navigator.clipboard.readText()) === expectedURL, 'ACTUAL_CANONICAL_CLIPBOARD_REQUIRED')
  await native(page, panel, labels.native, { title: labels.title, text: preview })
  if (cancel) {
    await native(page, panel, labels.native, { title: labels.title, text: preview }, true)
    await panel.getByText(labels.canceled, { exact: true }).waitFor()
  }
  linkChecks++
  return panel
}
function complete(locale, name) { journeys.push(locale + '_' + name) }

async function authRoundtrip(locale, copy, kind, path, hint, actor, targetName) {
  advance(locale + '_auth_' + kind)
  const context = await contextFor(locale), page = await context.newPage()
  await page.goto(DASH + path, { waitUntil: 'domcontentloaded' })
  await page.waitForURL(url => url.origin === LANDING && url.pathname.replace(/\/$/, '') === '/login')
  await foreground(page)
  const expected = new URLSearchParams(hint), current = new URL(page.url()).searchParams
  check([...expected].every(([key, value]) => current.getAll(key).length === 1 && current.get(key) === value), 'ACTUAL_LOGIN_TARGET_REQUIRED')
  await button(page, copy.auth.login.form.signup_cta).click()
  await page.waitForURL(url => url.pathname.replace(/\/$/, '') === '/signup')
  check([...expected].every(([key, value]) => new URL(page.url()).searchParams.get(key) === value), 'ACTUAL_SIGNUP_TARGET_REQUIRED')
  await button(page, copy.auth.signup.form.login_cta).first().click()
  await page.waitForURL(url => url.pathname.replace(/\/$/, '') === '/login')
  await page.locator('input[type=email]').fill(accounts[actor].email)
  await page.locator('input[type=password]').fill(accounts[actor].password)
  const [reply] = await Promise.all([page.waitForResponse(response => new URL(response.url()).origin === API
    && new URL(response.url()).pathname === '/auth/login' && response.request().method() === 'POST'), button(page, copy.auth.login.form.submit).last().click()])
  check(reply.status() === 200, 'ACTUAL_UI_LOGIN_REQUIRED')
  await page.waitForURL(url => url.origin === DASH && url.pathname + url.search === path)
  await page.getByRole('heading', { name: targetName, exact: true }).first().waitFor(); await foreground(page)
  complete(locale, 'auth_' + kind)
}
async function localeJourneys(locale, copy) {
  advance(locale + '_private_contact')
  const admin = await contextFor(locale, 'admin'), member = await contextFor(locale, 'member')
  const adminPage = await admin.newPage(), memberPage = await member.newPage()
  const contactPath = '/trade-contacts?workspace=admin&contact=' + facts.contactId
  await navigate(adminPage, contactPath)
  const record = adminPage.getByRole('dialog', { name: copy.share.linked_contact, exact: true })
  await record.getByRole('heading', { name: facts.contactName, exact: true }).waitFor()
  await sharedLink(adminPage, record, profileLabels(copy, 'contact'), APP_CANONICAL + contactPath, true)
  await geometry(adminPage, record, locale + '_private_contact'); complete(locale, 'private_contact')

  advance(locale + '_contact_preview')
  await button(record, copy.share.open).click()
  const dialog = adminPage.getByRole('dialog', { name: copy.share.title, exact: true })
  const fields = dialog.locator('fieldset input[type=checkbox]'), permission = dialog.getByLabel(copy.share.permission, { exact: true })
  check(await fields.nth(0).isChecked() && !await fields.nth(1).isChecked() && !await fields.nth(2).isChecked()
    && !await permission.isChecked() && await button(dialog, copy.share.copy).isDisabled()
    && await button(dialog, copy.share.native_share).isDisabled(), 'EXPLICIT_PERMISSION_GATE_REQUIRED')
  check(await dialog.locator('pre').innerText() === copy.share.name + ': ' + facts.contactName
    && await dialog.locator('a').count() === 0, 'NAME_ONLY_DEFAULT_REQUIRED')
  await fields.nth(1).check(); await fields.nth(2).check(); await permission.check()
  const preview = copy.share.name + ': ' + facts.contactName + '\n' + copy.share.email + ': ' + facts.contactEmail
    + '\n' + copy.share.phone + ': ' + facts.contactPhone
  check(await dialog.locator('pre').innerText() === preview && !preview.includes(facts.privateNote), 'EXACT_CONTACT_SELECTION_REQUIRED')
  await composers(dialog, { email: copy.share.compose_email, whatsapp: copy.share.compose_whatsapp }, preview, copy.share.subject)
  await button(dialog, copy.share.copy).click(); await dialog.getByText(copy.share.copied, { exact: true }).waitFor()
  check(await adminPage.evaluate(() => navigator.clipboard.readText()) === preview, 'ACTUAL_CONTACT_CLIPBOARD_REQUIRED')
  await native(adminPage, dialog, copy.share.native_share, { text: preview })
  await geometry(adminPage, dialog, locale + '_contact_review')
  await adminPage.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' })
  check(await record.isVisible(), 'NESTED_ESCAPE_BOUNDARY_REQUIRED'); complete(locale, 'contact_preview')

  advance(locale + '_admin_user_contact')
  await navigate(adminPage, '/users?workspace=admin&openId=' + facts.memberId)
  const userPanel = adminPage.locator('div.fixed.inset-y-0.right-0')
  await button(userPanel, copy.share.open).click()
  const userDialog = adminPage.getByRole('dialog', { name: copy.share.title, exact: true })
  check(await userDialog.locator('fieldset input[type=checkbox]').nth(1).isChecked() === false
    && !await userDialog.getByLabel(copy.share.permission, { exact: true }).isChecked(), 'ADMIN_USER_EXPLICIT_SELECTION_REQUIRED')
  await geometry(adminPage, userDialog, locale + '_admin_user_contact')
  await adminPage.keyboard.press('Escape'); await userDialog.waitFor({ state: 'hidden' })
  await sharedLink(adminPage, userPanel, profileLabels(copy), APP_CANONICAL + '/property-network?tab=people&member=' + facts.memberCode)
  complete(locale, 'admin_user_contact')

  advance(locale + '_member_profile')
  const realtorPath = '/property-network?tab=people&member=' + facts.realtorCode
  await navigate(memberPage, realtorPath)
  await memberPage.getByRole('heading', { name: facts.realtorName, exact: true }).waitFor()
  const profilePanel = await sharedLink(memberPage, memberPage, profileLabels(copy), APP_CANONICAL + realtorPath)
  await geometry(memberPage, profilePanel, locale + '_member_profile')
  await read(member, '/admin/trade-contacts/' + facts.contactId, 403)
  await read(member, '/professionals/' + facts.hiddenProfileId, 404)
  await read(member, '/property-network/profiles/' + facts.privateCode, 404)
  await read(member, '/property-network/profiles/' + facts.blockedCode, 404)
  await navigate(memberPage, '/property-network?tab=people')
  const realtorCard = memberPage.locator('article').filter({ has: memberPage.getByRole('heading', { name: facts.realtorName, exact: true }) })
  await realtorCard.waitFor()
  check(await memberPage.getByText(facts.privateName, { exact: true }).count() === 0
    && await memberPage.getByText(facts.blockedName, { exact: true }).count() === 0, 'DISCOVERY_PRIVACY_REQUIRED')
  await sharedLink(memberPage, realtorCard, profileLabels(copy), APP_CANONICAL + realtorPath)
  complete(locale, 'member_realtor_directory')

  advance(locale + '_own_member')
  await navigate(memberPage, '/property-network?tab=my-profile')
  await memberPage.getByRole('heading', { name: facts.memberName, exact: true }).waitFor()
  await sharedLink(memberPage, memberPage, profileLabels(copy), APP_CANONICAL + '/property-network?tab=people&member=' + facts.memberCode)
  complete(locale, 'own_member')

  advance(locale + '_professional_profile')
  const professionalPath = '/professional-directory?profile=' + facts.managedProfileId
  await navigate(memberPage, professionalPath)
  const professional = memberPage.getByRole('dialog', { name: facts.managedName, exact: true })
  await professional.getByRole('heading', { name: facts.managedName, exact: true }).last().waitFor()
  await sharedLink(memberPage, professional, profileLabels(copy), APP_CANONICAL + professionalPath)
  await geometry(memberPage, professional, locale + '_professional_profile')
  await read(member, '/professionals/' + facts.ownedProfileId)
  await navigate(memberPage, '/professional-profile')
  const published = memberPage.locator('section').filter({ has: memberPage.getByRole('heading', { name: copy.professionals.public_preview, exact: true }) })
  await published.getByRole('heading', { name: facts.ownedName, exact: true }).waitFor()
  await sharedLink(memberPage, published, profileLabels(copy), APP_CANONICAL + '/professional-directory?profile=' + facts.ownedProfileId)
  complete(locale, 'approved_professionals')

  advance(locale + '_public_team')
  const teamContext = await contextFor(locale), teamPage = await teamContext.newPage()
  await teamPage.goto(LANDING + '/team/#member-amiel', { waitUntil: 'domcontentloaded' }); await foreground(teamPage)
  const teamLabels = copy.team.share
  const principal = teamPage.locator('#member-amiel'), realtor = teamPage.locator('#member-francy')
  await principal.getByRole('heading', { name: 'Amiel Ibrahim', exact: true }).waitFor()
  await sharedLink(teamPage, principal, teamLabels, SITE_CANONICAL + '/team#member-amiel')
  await geometry(teamPage, principal, locale + '_public_team')
  await sharedLink(teamPage, realtor, teamLabels, SITE_CANONICAL + '/team#member-francy')
  complete(locale, 'public_team')
  await authRoundtrip(locale, copy, 'member', realtorPath, 'member_profile=' + facts.realtorCode, 'member', facts.realtorName)
  await authRoundtrip(locale, copy, 'professional', professionalPath, 'professional_profile=' + facts.managedProfileId, 'member', facts.managedName)
  await authRoundtrip(locale, copy, 'contact', contactPath, 'trade_contact=' + facts.contactId, 'admin', facts.contactName)
}

try {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api', 'EXACT_HOSTED_BROWSER_REQUIRED')
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'EXACT_PRIVATE_FACTS_PATH_REQUIRED')
  const info = await lstat(path)
  check(info.isFile() && !info.isSymbolicLink() && (info.mode & 0o777) === 0o600 && info.size > 0 && info.size < 16384, 'OWNED_PRIVATE_FACTS_REQUIRED')
  const fixture = JSON.parse(await readFile(path, 'utf8'))
  check(fixture.scope === 'contact-profile-sharing-read-only' && fixture.accounts && fixture.facts, 'EXACT_SHARING_FACTS_REQUIRED')
  ;({ accounts, facts } = fixture)
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage', '--no-sandbox'] })
  for (const locale of ['en', 'es']) {
    const copy = JSON.parse(await readFile(new URL('./' + locale + '.json', import.meta.url), 'utf8'))
    await localeJourneys(locale, copy)
  }
  advance('final')
  check(journeys.length === 20 && new Set(journeys).size === 20 && geometryChecks.length === 24
    && new Set(geometryChecks.map(row => row.label + '_' + row.width)).size === 24
    && contexts.length === 12 && presenceCount === 12 && linkChecks === 18 && nativeCalls === 22,
    'ALL_FINITE_JOURNEYS_REQUIRED')
  check(pageErrors === 0 && externalWrites === 0 && unexpectedMutations === 0 && trackingRequests === 0, 'NO_UNEXPECTED_ACTIVITY_REQUIRED')
  console.log('SHARING_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', locales: ['en', 'es'], journeys: 20,
    geometry: 24, link_checks: 18, native_calls: 22, native_share_mode: 'instrumented_no_os_delivery',
    page_errors: pageErrors, unexpected_mutations: unexpectedMutations, external_writes: externalWrites,
    tracking_requests: trackingRequests, blocked_external_requests: blockedExternal }))
} catch (error) {
  const reason = error instanceof Failure ? error.message : 'UNEXPECTED_' + (['Error', 'TypeError', 'TimeoutError'].includes(error?.name) ? error.name : 'Error')
  console.log('SHARING_BROWSER_FAILURE ' + stage + ' ' + reason); process.exitCode = 1
} finally {
  clearTimeout(timer)
  for (const context of contexts.reverse()) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
}
