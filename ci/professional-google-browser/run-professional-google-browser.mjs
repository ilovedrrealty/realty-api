import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const API = 'http://127.0.0.1:8918', DASH = 'http://127.0.0.1:4175'
const checks = ['owner_google_validation_private_save', 'admin_managed_google_private_edit',
  'explicit_review_publication_and_audience', 'manual_embed_load_hide_and_external_fallback',
  'approved_snapshot_and_source_change_remount', 'consent_withdrawal_and_legacy_blank_compatibility']
const contexts = [], geometryChecks = [], allowedGoogle = new WeakSet()
let browser, facts, stage = 'fixture', timer, frameHold = null, frameBudget = 0, profileBudget = 0
let pageErrors = 0, externalWrites = 0, unexpectedMutations = 0, beforeClick = 0
let frameRequests = 0, profileRequests = 0, authScripts = 0, ownerPuts = 0
class Failure extends Error {}
const check = (condition, reason) => { if (!condition) throw new Failure(reason) }
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const matches = (response, path, method) => new URL(response.url()).origin === API
  && new URL(response.url()).pathname === path && (!method || response.request().method() === method)
function advance(value) {
  stage = value; clearTimeout(timer); console.log('PROFESSIONAL_GOOGLE_BROWSER_STAGE ' + value)
  timer = setTimeout(() => { console.log('PROFESSIONAL_GOOGLE_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
async function read(context, path, expected = 200) {
  const response = await context.request.get(API + path, { headers: { Origin: DASH }, maxRedirects: 0, timeout: 15000 })
  try { check(response.status() === expected, 'ACTUAL_READ_STATUS_REQUIRED'); return await response.json() }
  finally { await response.dispose() }
}
async function contextFor(account, role, storageState) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    serviceWorkers: 'block', ...(storageState ? { storageState } : {}) })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', () => { pageErrors++ }))
  context.on('request', request => {
    const url = new URL(request.url())
    if (url.origin === API && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      const allowed = (request.method() === 'PUT' && url.pathname === '/professional-profile')
        || (request.method() === 'POST' && url.pathname === '/professional-profile/submit')
        || (request.method() === 'PUT' && url.pathname === `/admin/professionals/${facts.managedProfileId}`)
        || (request.method() === 'POST' && url.pathname === `/admin/professionals/${facts.ownerProfileId}/approve`)
      if (!allowed) unexpectedMutations++
      if (request.method() === 'PUT' && url.pathname === '/professional-profile') ownerPuts++
    }
    if (url.href === facts.embedUrl) {
      if (frameBudget === 1) { frameBudget = 0; frameRequests++; allowedGoogle.add(request) }
      else beforeClick++
    } else if ([facts.profileUrl, facts.nextProfileUrl].includes(url.href)) {
      if (profileBudget === 1) { profileBudget = 0; profileRequests++; allowedGoogle.add(request) }
      else beforeClick++
    } else if (url.origin === 'https://accounts.google.com' && url.pathname === '/gsi/client') authScripts++
  })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if ([API, DASH].includes(url.origin)) return route.continue()
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) externalWrites++
    if (url.href === facts.embedUrl && allowedGoogle.has(request)) {
      check(frameHold, 'MANUAL_FRAME_HOLD_REQUIRED')
      frameHold.ready()
      await frameHold.released
    }
    // No external response is fetched or fulfilled, including Google content.
    await route.abort('blockedbyclient').catch(() => {})
  })
  await context.addInitScript(origin => {
    if (location.origin !== origin) return
    if (!localStorage.getItem('ildr_lang')) localStorage.setItem('ildr_lang', 'en')
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
    const native = navigator.onLine
    if (!native) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__ildrGoogleNativeOnline', { value: native })
  }, DASH)
  if (account) {
    const response = await context.request.post(API + '/auth/login', { data: account, headers: { Origin: DASH }, maxRedirects: 0, timeout: 15000 })
    try { check(response.status() === 200 && (await context.cookies(API)).some(row => row.name === 'ildr_token' && row.httpOnly), 'ACTUAL_COOKIE_REQUIRED') }
    finally { await response.dispose() }
    check((await read(context, '/auth/me')).role === role, 'ACTUAL_ROLE_REQUIRED')
  }
  return context
}
async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}
async function openMenu(context, copy, item) {
  const page = await context.newPage()
  advance('entry_document')
  await page.goto(DASH + '/', { waitUntil: 'domcontentloaded' }); await foreground(page)
  const state = await page.evaluate(() => ({ before: window.__ildrGoogleNativeOnline,
    modeled: !window.__ildrGoogleNativeOnline, after: navigator.onLine, visible: document.visibilityState === 'visible' }))
  check(Object.values(state).every(flag => typeof flag === 'boolean') && state.after && state.visible, 'ACTUAL_LOOPBACK_PRESENCE_REQUIRED')
  console.log('PROFESSIONAL_GOOGLE_LOOPBACK_STATE ' + JSON.stringify(state))
  advance('entry_menu')
  await page.locator('button:has(svg.lucide-menu)').click()
  await button(page.locator('aside'), copy.nav[item]).click()
  if (item === 'professional-profile') await page.getByLabel(copy.professionals.google.profile_url, { exact: true }).waitFor()
  if (item === 'professionals') await page.getByLabel(copy.professionals.search, { exact: true }).waitFor()
  if (item === 'professional-directory') await page.getByRole('heading', { name: copy.professionals.directory_search.title, exact: true }).waitFor()
  return page
}
async function language(page, copy) {
  await foreground(page)
  await page.locator('button:has(svg.lucide-menu)').click()
  await page.locator('aside').getByRole('button').filter({ hasText: copy.nav.lang_toggle }).click()
  await page.locator('aside button:has(svg.lucide-x)').click()
}
async function write(page, path, method, perform, expected = 200) {
  await foreground(page)
  const [response] = await Promise.all([page.waitForResponse(value => matches(value, path, method)), perform()])
  check(response.status() === expected, 'ACTUAL_MUTATION_STATUS_REQUIRED')
  return { value: await response.json(), input: response.request().postDataJSON() }
}
async function saveOwner(page, copy, expectedVersion, expectedStatus = 200) {
  const result = await write(page, '/professional-profile', 'PUT', () => button(page, copy.professionals.save).click(), expectedStatus)
  check(result.input.expected_version === expectedVersion, 'ACTUAL_CAS_VERSION_REQUIRED')
  if (expectedStatus === 200) {
    check(result.value.version === expectedVersion + 1, 'SAVED_VERSION_INCREMENT_REQUIRED')
    await page.getByText(copy.professionals.saved, { exact: true }).waitFor()
    await page.locator('form fieldset:not([disabled])').waitFor()
    check(await button(page, copy.professionals.save).isDisabled(), 'SAVED_DRAFT_BASELINE_REQUIRED')
  }
  return result.value
}
async function submitOwner(page, copy, expectedVersion) {
  await foreground(page)
  const saved = page.waitForResponse(response => matches(response, '/professional-profile', 'PUT'))
  const submitted = page.waitForResponse(response => matches(response, '/professional-profile/submit', 'POST'))
  await button(page, copy.professionals.submit).click()
  const [put, post] = await Promise.all([saved, submitted])
  check(put.status() === 200 && post.status() === 200 && put.request().postDataJSON().expected_version === expectedVersion
    && post.request().postDataJSON().expected_version === expectedVersion + 1, 'EXPLICIT_SAVE_THEN_SUBMIT_REQUIRED')
  const result = await post.json()
  check(result.version === expectedVersion + 2 && result.review_status === 'pending', 'ACTUAL_REVIEW_SUBMISSION_REQUIRED')
  await page.getByText(copy.professionals.submitted, { exact: true }).waitFor()
  return result
}
async function searchAdmin(page, copy, name, id) {
  await foreground(page)
  await page.getByLabel(copy.professionals.search, { exact: true }).fill(name)
  const [response] = await Promise.all([page.waitForResponse(value => matches(value, '/admin/professionals', 'GET')),
    button(page, copy.professionals.search_apply).click()])
  check(response.status() === 200, 'ACTUAL_ADMIN_SEARCH_REQUIRED')
  const card = page.getByRole('heading', { name, exact: true, level: 2 }).locator('xpath=ancestor::article[1]')
  const [detail] = await Promise.all([page.waitForResponse(value => matches(value, `/admin/professionals/${id}`, 'GET')),
    button(card, copy.professionals.review_profile).click()])
  check(detail.status() === 200 && (await detail.json()).id === id, 'ACTUAL_REVIEW_DETAIL_REQUIRED')
  return page.getByRole('dialog', { name: copy.professionals.review_profile, exact: true })
}
async function approve(page, copy, expectedVersion) {
  const dialog = page.getByRole('dialog', { name: copy.professionals.review_profile, exact: true })
  await dialog.getByLabel(copy.professionals.reason, { exact: true }).fill(facts.reviewNote)
  await button(dialog, copy.professionals.approve).click()
  const confirmation = page.getByRole('dialog', { name: copy.professionals.approve, exact: true })
  const result = await write(page, `/admin/professionals/${facts.ownerProfileId}/approve`, 'POST', () => button(confirmation, copy.professionals.confirm).click())
  check(result.input.expected_version === expectedVersion && result.input.note === facts.reviewNote
    && result.value.version === expectedVersion + 1 && result.value.listing_status === 'published', 'EXPLICIT_APPROVED_VERSION_REQUIRED')
  await button(page.getByRole('dialog'), copy.professionals.close).click()
  return result.value
}
async function openMember(page, copy, name, id) {
  await foreground(page)
  const card = page.getByRole('heading', { name, level: 2, exact: true }).locator('xpath=ancestor::article[1]')
  const [response] = await Promise.all([page.waitForResponse(value => matches(value, `/professionals/${id}`, 'GET')),
    button(card, `${copy.professionals.directory_search.view}: ${name}`).click()])
  check(response.status() === 200 && (await response.json()).id === id, 'ACTUAL_MEMBER_DETAIL_REQUIRED')
  const dialog = page.getByRole('dialog', { name, exact: true })
  await dialog.getByRole('heading', { level: 3, name, exact: true }).waitFor()
  return dialog
}
async function geometry(page, label) {
  await foreground(page)
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    const result = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(result.width === width && result.document <= width + 1, 'WHOLE_PAGE_OVERFLOW')
    const dialog = page.getByRole('dialog')
    if (await dialog.count()) check(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1), 'DIALOG_HORIZONTAL_OVERFLOW')
    geometryChecks.push({ label, width }); console.log('PROFESSIONAL_GOOGLE_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...result }))
  }
}
function googleSection(dialog, copy) { return dialog.getByRole('heading', { name: copy.professionals.google.heading, exact: true, level: 4 }).locator('..') }
async function loadMap(page, section, copy) {
  await foreground(page)
  check(await section.locator('iframe').count() === 0, 'NO_FRAME_BEFORE_MANUAL_CLICK')
  let ready, release
  const waiting = new Promise(resolve => { ready = resolve }), released = new Promise(resolve => { release = resolve })
  frameHold = { ready, released, release }; frameBudget = 1
  await button(section, copy.professionals.google.load_map).click()
  const frame = section.locator('iframe')
  await frame.waitFor(); await frame.scrollIntoViewIfNeeded(); await waiting
  check(await frame.count() === 1 && await frame.getAttribute('src') === facts.embedUrl
    && await frame.getAttribute('sandbox') === 'allow-scripts allow-same-origin', 'MANUAL_EXACT_FRAME_REQUIRED')
  return async () => { release(); frameHold = null }
}
async function fallback(context, page, section, copy) {
  const link = section.getByRole('link', { name: copy.professionals.google.open_profile, exact: true })
  check(await link.getAttribute('href') === facts.nextProfileUrl && await link.getAttribute('target') === '_blank'
    && /noopener/.test(await link.getAttribute('rel') || '') && /noreferrer/.test(await link.getAttribute('rel') || ''), 'SAFE_PROFILE_FALLBACK_REQUIRED')
  profileBudget = 1
  const [popup, request] = await Promise.all([context.waitForEvent('page'),
    context.waitForEvent('request', value => value.url() === facts.nextProfileUrl && value.isNavigationRequest()), link.click()])
  check(request.url() === facts.nextProfileUrl && profileRequests === 1 && profileBudget === 0, 'MANUAL_PROFILE_NAVIGATION_REQUIRED')
  await popup.close()
}

try {
  advance('fixture')
  check(process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === '/browser-fixtures/fixtures.json'
    && JSON.stringify(await readdir('/sys/class/net')) === '["lo"]', 'OWNED_LOOPBACK_REQUIRED')
  const file = await lstat(process.env.ILDR_BROWSER_FIXTURES)
  check(file.isFile() && !file.isSymbolicLink() && (file.mode & 0o777) === 0o600 && file.size < 100000, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(process.env.ILDR_BROWSER_FIXTURES, 'utf8'))
  check(fixture.scope === 'professional-google-only', 'EXACT_FIXTURE_SCOPE_REQUIRED'); facts = fixture.facts
  const en = JSON.parse(await readFile('/browser-run/en.json', 'utf8')), es = JSON.parse(await readFile('/browser-run/es.json', 'utf8'))
  browser = await chromium.launch({ headless: true })
  const owner = await contextFor(fixture.accounts.owner, 'owner'), admin = await contextFor(fixture.accounts.admin, 'admin')
  const member = await contextFor(fixture.accounts.member, 'buyer'), guest = await contextFor()
  const ownerPage = await openMenu(owner, en, 'professional-profile')

  advance('G01_owner_validation')
  await ownerPage.getByLabel(en.professionals.google.profile_url, { exact: true }).fill(facts.invalidProfileUrl)
  await button(ownerPage, en.professionals.save).click()
  await ownerPage.getByRole('alert').getByText(en.professionals.issues.google_profile_url, { exact: true }).waitFor()
  check(ownerPuts === 0 && (await read(owner, '/professional-profile')).version === 1, 'INVALID_GOOGLE_MUST_NOT_SAVE')
  await ownerPage.getByLabel(en.professionals.google.profile_url, { exact: true }).fill(facts.profileUrl)
  await ownerPage.getByLabel(en.professionals.google.embed_url, { exact: true }).fill(facts.embedUrl)
  const first = await saveOwner(ownerPage, en, 1)
  check(first.listing_status === 'hidden' && !first.draft.public_contact_consent, 'PRIVATE_GOOGLE_SAVE_REQUIRED')
  await geometry(ownerPage, 'en_owner_editor')
  await language(ownerPage, en); await ownerPage.getByLabel(es.professionals.google.embed_url, { exact: true }).waitFor()
  await language(ownerPage, es)
  const peer = await contextFor(null, null, await owner.storageState()), peerPage = await openMenu(peer, en, 'professional-profile')
  await peerPage.getByLabel(en.professionals.google.profile_url, { exact: true }).fill(facts.nextProfileUrl)
  await peerPage.getByLabel(en.professionals.google.embed_url, { exact: true }).fill('')

  advance('G02_managed_private_editor')
  const adminPage = await openMenu(admin, en, 'professionals')
  await searchAdmin(adminPage, en, facts.managedName, facts.managedProfileId)
  await button(adminPage.getByRole('dialog'), en.professionals.managed_edit).click()
  await adminPage.getByRole('dialog', { name: en.professionals.managed_edit, exact: true }).getByLabel(en.professionals.google.profile_url, { exact: true }).waitFor()
  await button(adminPage.getByRole('dialog'), en.professionals.managed_close).click()
  await button(adminPage.getByRole('dialog'), en.professionals.close).click()
  await language(adminPage, en)
  await searchAdmin(adminPage, es, facts.managedName, facts.managedProfileId)
  await button(adminPage.getByRole('dialog'), es.professionals.managed_edit).click()
  const managed = adminPage.getByRole('dialog', { name: es.professionals.managed_edit, exact: true })
  await managed.getByLabel(es.professionals.google.profile_url, { exact: true }).fill(facts.profileUrl)
  await managed.getByLabel(es.professionals.google.embed_url, { exact: true }).fill(facts.embedUrl)
  await geometry(adminPage, 'es_managed_editor')
  const managedSaved = await write(adminPage, `/admin/professionals/${facts.managedProfileId}`, 'PUT', () => button(managed, es.professionals.save).click())
  check(managedSaved.input.expected_version === 1 && managedSaved.value.version === 2 && managedSaved.value.published_profile === null
    && managedSaved.value.listing_status === 'hidden', 'MANAGED_SAVE_MUST_STAY_PRIVATE')
  await managed.getByText(es.professionals.managed_saved, { exact: true }).waitFor()
  await button(managed, es.professionals.managed_close).click(); await button(adminPage.getByRole('dialog'), es.professionals.close).click()
  await read(member, `/professionals/${facts.managedProfileId}`, 404)

  advance('G03_explicit_review_and_cas')
  await foreground(ownerPage); await ownerPage.getByLabel(en.professionals.consent, { exact: true }).check()
  await submitOwner(ownerPage, en, 2)
  await saveOwner(peerPage, en, 2, 409)
  await peerPage.getByRole('alert').getByText(en.professionals.conflict, { exact: true }).waitFor()
  check(await peerPage.getByLabel(en.professionals.google.profile_url, { exact: true }).inputValue() === facts.nextProfileUrl, 'STALE_DRAFT_MUST_BE_RETAINED')
  await read(member, `/professionals/${facts.ownerProfileId}`, 404)
  await read(guest, '/professionals?limit=24&offset=0', 401)
  await searchAdmin(adminPage, es, facts.ownerName, facts.ownerProfileId); await approve(adminPage, es, 4)
  const memberPage = await openMenu(member, en, 'professional-directory')
  const legacy = await openMember(memberPage, en, facts.legacyName, facts.legacyProfileId)
  check(await legacy.getByRole('heading', { name: en.professionals.google.heading, exact: true }).count() === 0
    && await legacy.locator('iframe').count() === 0, 'LEGACY_GOOGLE_FIELDS_MUST_REMAIN_BLANK')
  await button(legacy, en.professionals.directory_search.close).click()
  let viewer = await openMember(memberPage, en, facts.ownerName, facts.ownerProfileId)
  let google = googleSection(viewer, en)
  check(await google.locator('iframe').count() === 0 && beforeClick === 0 && frameRequests === 0, 'NO_BUSINESS_GOOGLE_CONNECTION_BEFORE_CLICK')
  await geometry(memberPage, 'en_member_view')

  advance('G04_manual_load_and_hide')
  let releaseFrame = await loadMap(memberPage, google, en)
  await button(google, en.professionals.google.hide_map).click(); await google.locator('iframe').waitFor({ state: 'detached' }); await releaseFrame()
  await button(viewer, en.professionals.directory_search.close).click(); await language(memberPage, en)
  viewer = await openMember(memberPage, es, facts.ownerName, facts.ownerProfileId); google = googleSection(viewer, es)
  await geometry(memberPage, 'es_member_view')
  releaseFrame = await loadMap(memberPage, google, es)

  advance('G05_approved_snapshot_change')
  await foreground(ownerPage); await ownerPage.reload({ waitUntil: 'domcontentloaded' })
  await ownerPage.getByLabel(en.professionals.google.profile_url, { exact: true }).fill(facts.nextProfileUrl)
  await ownerPage.getByLabel(en.professionals.google.embed_url, { exact: true }).fill('')
  const edited = await saveOwner(ownerPage, en, 5)
  check(edited.published_profile.google_profile_url === facts.profileUrl, 'PENDING_EDIT_MUST_KEEP_APPROVED_SNAPSHOT')
  const oldPublic = await read(member, `/professionals/${facts.ownerProfileId}`)
  check(oldPublic.google_profile_url === facts.profileUrl && oldPublic.google_maps_embed_url === facts.embedUrl
    && await memberPage.locator('iframe').getAttribute('src') === facts.embedUrl, 'MEMBER_MUST_RETAIN_APPROVED_OLD_SOURCE')
  await submitOwner(ownerPage, en, 6)
  await searchAdmin(adminPage, es, facts.ownerName, facts.ownerProfileId); await approve(adminPage, es, 8)
  const current = memberPage.waitForResponse(response => matches(response, `/professionals/${facts.ownerProfileId}`, 'GET'))
  await foreground(memberPage); await memberPage.evaluate(() => window.dispatchEvent(new Event('focus')))
  const fresh = await current
  check(fresh.status() === 200 && (await fresh.json()).google_profile_url === facts.nextProfileUrl, 'APPROVED_NEW_SOURCE_REQUIRED')
  viewer = memberPage.getByRole('dialog', { name: facts.ownerName, exact: true }); google = googleSection(viewer, es)
  await memberPage.waitForFunction(expected => [...document.querySelectorAll('[role="dialog"] a')]
    .some(link => link.getAttribute('href') === expected), facts.nextProfileUrl)
  check(await google.locator('iframe').count() === 0 && await button(google, es.professionals.google.load_map).count() === 0, 'SOURCE_CHANGE_MUST_REMOVE_OLD_EMBED')
  await releaseFrame(); await fallback(member, memberPage, google, es)

  advance('G06_consent_withdrawal')
  const revoked = memberPage.waitForResponse(response => matches(response, `/professionals/${facts.ownerProfileId}`, 'GET') && response.status() === 404)
  await foreground(ownerPage); await ownerPage.reload({ waitUntil: 'domcontentloaded' })
  await ownerPage.getByLabel(en.professionals.consent, { exact: true }).uncheck()
  const hidden = await saveOwner(ownerPage, en, 9)
  check(hidden.version === 10 && hidden.listing_status === 'hidden' && hidden.draft.public_contact_consent === false, 'CONSENT_MUST_HIDE_PROFILE')
  await foreground(memberPage); await memberPage.evaluate(() => window.dispatchEvent(new Event('focus'))); await revoked
  await memberPage.getByRole('dialog').getByText(es.professionals.directory_search.unavailable, { exact: true }).waitFor()
  check(await memberPage.locator('iframe').count() === 0 && await memberPage.getByRole('link', { name: es.professionals.google.open_profile, exact: true }).count() === 0, 'WITHDRAWAL_MUST_CLEAR_GOOGLE_LINKS')
  await read(member, `/professionals/${facts.ownerProfileId}`, 404)
  const legacyAgain = await read(member, `/professionals/${facts.legacyProfileId}`)
  check((legacyAgain.google_profile_url ?? '') === '' && (legacyAgain.google_maps_embed_url ?? '') === '', 'LEGACY_REMAINS_UNCHANGED')
  check(geometryChecks.length === 8 && pageErrors === 0 && unexpectedMutations === 0 && externalWrites === 0
    && beforeClick === 0 && frameRequests === 2 && profileRequests === 1, 'EXACT_BROWSER_SCOPE_REQUIRED')
  clearTimeout(timer)
  console.log('PROFESSIONAL_GOOGLE_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks, geometry_checks: 8, locales: ['en', 'es'],
    page_errors: pageErrors, unexpected_mutations: unexpectedMutations, external_writes: externalWrites,
    business_google_requests_before_click: beforeClick, live_google_fetches: 0,
    manual_frame_requests: frameRequests, manual_profile_requests: profileRequests, blocked_auth_script_requests: authScripts }))
} catch (cause) {
  console.log('PROFESSIONAL_GOOGLE_BROWSER_FAILURE ' + stage + ' ' + (cause instanceof Failure ? cause.message : 'LOCATOR_OR_EVENT_DEADLINE'))
  process.exitCode = 1
} finally {
  clearTimeout(timer)
  if (frameHold) frameHold.release()
  for (const context of contexts) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
}
