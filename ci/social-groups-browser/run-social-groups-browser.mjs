import { lstat, readFile, readdir } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const api = 'http://127.0.0.1:8918', dashboard = 'http://127.0.0.1:4175', landing = 'http://127.0.0.1:8917'
const prefix = '/property-network'
const checks = ['normal_menu_private_create_and_draft_cancel', 'direct_invitation_decline_reinvite_and_alias_acceptance',
  'private_photos_group_image_and_photo_comments', 'group_roles_and_moderation', 'manual_fragment_invites_signin_accept_revoke',
  'registered_discovery_request_and_approval', 'publication_review_and_private_draft_version_binding',
  'open_join_leave_without_property_grants', 'withdrawal_and_next_request_photo_denial', 'manager_history_unblock_and_reinvite',
  'archive_and_closed_group_access_revocation', 'auth_loss_uncertain_status_and_second_invite_isolation']
const contexts = [], geometries = [], tokens = new Set(), invitePages = new Set()
let browser, timer, stage = 'fixture', pageErrors = 0, externalWrites = 0, trackingRequests = 0
let automaticAccepts = 0, tokenLeaks = 0, humanAcceptBudget = 0, linkAcceptRequests = 0, inviteProviders = 0
let entryPage = null, entryCopy = null, entryInProgress = true
const responseCounts = new Map()
const errorTypes = { Error: 0, TypeError: 0, ReferenceError: 0, SyntaxError: 0, RangeError: 0, other: 0 }
class Failure extends Error {}
const check = (value, reason) => { if (!value) throw new Failure(reason) }
function advance(value) {
  stage = value; clearTimeout(timer); console.log('SOCIAL_GROUPS_BROWSER_STAGE ' + value)
  timer = setTimeout(() => { console.log('SOCIAL_GROUPS_BROWSER_FAILURE ' + stage + ' STAGE_DEADLINE'); process.exit(1) }, 60000)
}
async function bounded(promise) {
  let deadline
  try { return await Promise.race([promise, new Promise((_, reject) => { deadline = setTimeout(() => reject(new Failure('BODY_DEADLINE')), 15000) })]) }
  finally { clearTimeout(deadline) }
}
const json = response => bounded(response.json())
const button = (surface, name) => surface.getByRole('button', { name, exact: true })
const pathIs = (response, path, method) => new URL(response.url()).origin === api
  && new URL(response.url()).pathname === path && (!method || response.request().method() === method)
function noStore(response) { check((response.headers()['cache-control'] || '').split(',').some(value => value.trim() === 'no-store'), 'PRIVATE_NO_STORE_REQUIRED') }
const groupPath = id => `${prefix}/groups/${id}`
const quote = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
async function request(context, path, data) {
  return context.request.fetch(api + path, { method: data === undefined ? 'GET' : 'POST', data,
    headers: { Origin: dashboard }, timeout: 15000, maxRedirects: 0 })
}
async function login(context, account, role) {
  const reply = await request(context, '/auth/login', account)
  check(reply.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly), 'REAL_COOKIE_LOGIN_REQUIRED')
  await reply.dispose()
  const me = await request(context, '/auth/me')
  check(me.status() === 200 && (await json(me)).role === role, 'ACTUAL_REGISTERED_ROLE_REQUIRED'); await me.dispose()
}
async function session(locale, account, role, storageState) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    serviceWorkers: 'block', permissions: ['clipboard-read', 'clipboard-write'], ...(storageState ? { storageState } : {}) })
  contexts.push(context); context.setDefaultTimeout(15000); context.setDefaultNavigationTimeout(20000)
  context.on('page', page => page.on('pageerror', error => {
    pageErrors++
    errorTypes[Object.hasOwn(errorTypes, error.name) ? error.name : 'other']++
  }))
  context.on('response', response => {
    const url = new URL(response.url()), resource = response.request().resourceType()
    const name = url.origin === api ? ({ '/auth/me': 'auth_me', '/professionals/options': 'professional_options',
      [`${prefix}/groups`]: 'groups' })[url.pathname] : url.origin === dashboard
      ? resource === 'document' ? 'dashboard_document' : resource === 'script' ? 'dashboard_script' : resource === 'stylesheet' ? 'dashboard_stylesheet' : null : null
    if (name) {
      const key = name + ':' + response.status()
      responseCounts.set(key, Math.min(999, (responseCounts.get(key) || 0) + 1))
    }
  })
  // Passive request events include requests intercepted by a page-level race hold.
  context.on('request', req => {
    const url = new URL(req.url())
    if (/google-analytics|googletagmanager|doubleclick|facebook\.com\/tr/.test(url.href)) trackingRequests++
    if (invitePages.has(req.frame()?.page()) && /accounts\.google\.com\/gsi\//.test(url.href)) inviteProviders++
    for (const token of tokens) {
      if (url.href.includes(token) || Object.values(req.headers()).some(value => value.includes(token))) tokenLeaks++
      if ((req.postData() || '').includes(token) && ![
        `${prefix}/group-invites/resolve`, `${prefix}/group-invites/accept`,
      ].includes(url.pathname) && !/^\/property-network\/groups\/[a-f0-9-]+\/invite-links$/.test(url.pathname)) tokenLeaks++
    }
    if (url.origin === api && req.method() === 'POST' && url.pathname.endsWith('/invite-links')) {
      const body = req.postDataJSON()
      if (typeof body?.token === 'string') tokens.add(body.token)
    }
    if (url.origin === api && url.pathname === `${prefix}/group-invites/accept` && req.method() === 'POST') {
      linkAcceptRequests++
      if (humanAcceptBudget === 1) humanAcceptBudget = 0
      else automaticAccepts++
    }
  })
  await context.route('**/*', route => {
    const req = route.request(), url = new URL(req.url())
    if (![api, dashboard].includes(url.origin)) {
      if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method())) externalWrites++
      return route.abort('blockedbyclient')
    }
    return route.continue()
  })
  await context.addInitScript(({ locale, origin }) => {
    if (location.origin !== origin) return
    if (!localStorage.getItem('ildr_lang')) localStorage.setItem('ildr_lang', locale)
    localStorage.setItem('ildr_measurement_consent_v1', JSON.stringify({ choice: 'denied', savedAt: Date.now() }))
  }, { locale, origin: dashboard })
  if (account) await login(context, account, role)
  return context
}
async function write(page, path, perform, method = 'POST', expected = [200, 201]) {
  const [response] = await Promise.all([page.waitForResponse(value => pathIs(value, path, method)), perform()])
  check(expected.includes(response.status()), 'ACTUAL_ACTION_STATUS_REQUIRED'); noStore(response)
  const req = response.request()
  return { response, value: await json(response), input: (req.headers()['content-type'] || '').startsWith('application/json') ? req.postDataJSON() : null }
}
async function menuGroups(context, copy, role) {
  const page = await context.newPage()
  entryPage = page; entryCopy = copy; entryInProgress = true
  advance('entry_document')
  await page.goto(dashboard + '/?workspace=' + role, { waitUntil: 'domcontentloaded' })
  advance('entry_menu')
  await page.locator('button:has(svg.lucide-menu)').click()
  advance('entry_network_navigation')
  await button(page.locator('aside'), copy.nav['property-network']).click()
  advance('entry_network_heading')
  await page.getByRole('heading', { name: copy.network.heading, exact: true }).waitFor()
  advance('entry_groups_navigation')
  const [response] = await Promise.all([page.waitForResponse(value => pathIs(value, `${prefix}/groups`, 'GET')),
    button(page.getByRole('navigation', { name: copy.network.main_tabs, exact: true }), copy.network.main.groups).click()])
  check(response.status() === 200, 'NORMAL_GROUP_ENTRY_REQUIRED'); noStore(response)
  advance('entry_groups_heading')
  await page.getByRole('heading', { name: copy.network.groups.title, exact: true }).waitFor()
  entryInProgress = false
  return page
}
async function entryDiagnostic(cause) {
  const counts = { menu_buttons: 0, sidebars: 0, network_navigation: 0, network_headings: 0, groups_navigation: 0, groups_headings: 0 }
  let surface = 'unavailable', readyState = 'unavailable', censusAvailable = false
  if (entryPage && !entryPage.isClosed()) {
    const url = new URL(entryPage.url())
    surface = url.origin === dashboard ? url.pathname === '/' ? 'dashboard_root' : url.pathname === '/property-network' ? 'property_network' : 'dashboard_other'
      : url.origin === landing ? 'landing' : url.href === 'about:blank' ? 'blank' : 'other'
    const census = async () => {
      readyState = await entryPage.evaluate(() => document.readyState)
      const locators = [entryPage.locator('button:has(svg.lucide-menu)'), entryPage.locator('aside'),
        button(entryPage.locator('aside'), entryCopy.nav['property-network']),
        entryPage.getByRole('heading', { name: entryCopy.network.heading, exact: true }),
        button(entryPage.getByRole('navigation', { name: entryCopy.network.main_tabs, exact: true }), entryCopy.network.main.groups),
        entryPage.getByRole('heading', { name: entryCopy.network.groups.title, exact: true })]
      const values = await Promise.all(locators.map(locator => locator.count()))
      Object.keys(counts).forEach((key, index) => { counts[key] = Math.min(999, values[index]) })
      censusAvailable = true
    }
    let timeout
    try { await Promise.race([census(), new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('CENSUS_DEADLINE')), 1500) })]) }
    catch { /* Counts remain bounded and explicitly unavailable. */ }
    finally { clearTimeout(timeout) }
  }
  const failureType = ['TimeoutError', 'TypeError', 'ReferenceError', 'SyntaxError', 'Error', 'Failure'].includes(cause?.name) ? cause.name : 'other'
  const responses = [...responseCounts].sort(([a], [b]) => a.localeCompare(b)).slice(0, 36).map(([key, count]) => {
    const [name, status] = key.split(':'); return { name, status: Number(status), count }
  })
  console.log('SOCIAL_GROUPS_ENTRY_DIAGNOSTIC ' + JSON.stringify({ phase: stage, failure_type: failureType, surface,
    ready_state: readyState, census_available: censusAvailable, counts, page_errors: Math.min(999, pageErrors),
    page_error_types: Object.fromEntries(Object.entries(errorTypes).map(([key, count]) => [key, Math.min(999, count)])), responses }))
}
function workspace(page, facts) {
  return page.getByRole('heading', { name: facts.groupName, exact: true }).locator('..').locator('..')
}
async function openGroup(page, copy, facts) {
  const card = page.getByRole('heading', { name: facts.groupName, exact: true }).locator('xpath=ancestor::article[1]')
  await button(card, copy.network.groups.open).click()
  await page.getByRole('heading', { name: facts.groupName, exact: true, level: 2 }).waitFor()
  return workspace(page, facts)
}
async function tab(page, copy, facts, name) {
  await button(workspace(page, facts).getByRole('navigation', { name: copy.network.groups.workspace_tabs, exact: true }), copy.network.groups.tabs[name]).click()
}
async function refreshHeader(page, copy, facts, expected = 200) {
  const name = copy.network.refresh
  const [response] = await Promise.all([page.waitForResponse(value => pathIs(value, groupPath(facts.groupId), 'GET')),
    button(workspace(page, facts), name).first().click()])
  check(response.status() === expected, 'ACTUAL_HEADER_RECHECK_REQUIRED'); noStore(response)
  return expected === 200 ? json(response) : null
}
async function geometry(page, label, widths = [320, 390, 1366]) {
  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 })
    const value = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }))
    check(value.width === width && value.document <= width + 1, 'WHOLE_PAGE_OVERFLOW')
    geometries.push({ label, width }); console.log('SOCIAL_GROUPS_BROWSER_GEOMETRY ' + JSON.stringify({ label, ...value }))
  }
  await page.setViewportSize({ width: 390, height: 844 })
}
async function switchLanguage(page, copy) {
  await page.locator('button:has(svg.lucide-menu)').click()
  await page.locator('aside').getByRole('button').filter({ hasText: copy.nav.lang_toggle }).click()
  await page.locator('aside button:has(svg.lucide-x)').click()
}
function memberRow(page, copy, code) {
  const suffix = copy.network.groups.code_value.replace('{{code}}', String(code))
  return page.getByRole('heading', { level: 4 }).filter({ hasText: new RegExp(quote(suffix) + '$') }).locator('xpath=ancestor::article[1]')
}
async function confirm(page, perform) {
  page.once('dialog', dialog => dialog.accept())
  return perform()
}
async function ownInbox(page, copy) {
  await button(page.getByRole('navigation', { name: copy.network.groups.views_label, exact: true }), copy.network.groups.views.invitations).click()
  await page.getByLabel(copy.network.groups.membership_filter, { exact: true }).waitFor()
}
async function inviteMember(page, copy, facts, code) {
  await tab(page, copy, facts, 'members')
  const form = page.getByRole('heading', { name: copy.network.groups.direct_invite, exact: true }).locator('..')
  await form.getByLabel(copy.network.groups.member_code, { exact: true }).fill(String(code))
  const result = await write(page, `${groupPath(facts.groupId)}/members/${code}/actions`, () => button(form, copy.network.groups.invite).click())
  check(result.value.operation === 'invite' && result.value.current.state === 'invited', 'REAL_DIRECT_INVITATION_REQUIRED')
  return result.value
}
async function acceptInbox(page, copy, facts, alias) {
  const card = page.getByRole('heading', { name: facts.groupName, exact: true }).locator('..')
  await card.getByLabel(copy.network.groups.member_alias, { exact: true }).fill(alias)
  const result = await write(page, `${groupPath(facts.groupId)}/membership/actions`, () => button(card, copy.network.groups.accept_invite).click())
  check(result.value.operation === 'accept_invite' && result.value.current.state === 'active', 'EXPLICIT_ALIAS_ACCEPTANCE_REQUIRED')
  await button(card, copy.network.groups.accept_invite).waitFor({ state: 'hidden' })
  return result.value
}
async function changeRole(page, copy, facts, code, role) {
  const row = memberRow(page, copy, code)
  await row.getByLabel(copy.network.groups.change_role, { exact: true }).selectOption(role)
  const result = await write(page, `${groupPath(facts.groupId)}/members/${code}/actions`, () => button(row, copy.network.groups.save_role).click())
  check(result.value.operation === 'set_role' && result.value.current.role === role, 'REAL_GROUP_ROLE_REQUIRED')
  await row.getByText(new RegExp('^' + quote(copy.network.groups.roles[role]) + ' ·')).waitFor()
  return result.value
}
function postCard(page, body) { return page.getByText(body, { exact: true }).locator('xpath=ancestor::article[1]') }
async function refreshFeed(page, copy, facts) {
  const panel = page.getByRole('heading', { name: copy.network.groups.feed_title, exact: true }).locator('..')
  const [response] = await Promise.all([page.waitForResponse(value => pathIs(value, `${groupPath(facts.groupId)}/posts`, 'GET')),
    button(panel, copy.network.refresh).first().click()])
  check(response.status() === 200, 'ACTUAL_FEED_REFRESH_REQUIRED'); noStore(response)
}
async function createPost(page, copy, facts, body, photoPath) {
  const panel = page.getByRole('heading', { name: copy.network.groups.feed_title, exact: true }).locator('..')
  const form = panel.locator('form').first()
  await form.getByLabel(copy.network.groups.write_post, { exact: true }).fill(body)
  let media = null
  if (photoPath) {
    const uploaded = await write(page, `${groupPath(facts.groupId)}/media`, () => form.locator('input[type=file]').setInputFiles(photoPath))
    check(uploaded.value.operation === 'media_upload' && uploaded.value.current.kind === 'post', 'ACTUAL_PRIVATE_PHOTO_UPLOAD_REQUIRED')
    media = uploaded.value.current
  }
  const result = await write(page, `${groupPath(facts.groupId)}/posts`, () => button(form, copy.network.groups.publish_post).click())
  check(result.value.operation === 'post_create' && result.input.expected_group_version >= 1, 'BOUND_GROUP_VERSION_REQUIRED')
  await postCard(page, body).waitFor()
  if (media) await postCard(page, body).getByRole('img', { name: copy.network.groups.photo_alt, exact: true }).waitFor()
  return { post: result.value.current, media }
}
async function hold(page, path, method, predicate, mode = 'response') {
  let ready, release, finished, used = false, captured
  const readyPromise = new Promise(resolve => { ready = resolve })
  const releasePromise = new Promise(resolve => { release = resolve })
  const finishedPromise = new Promise(resolve => { finished = resolve })
  const handler = async route => {
    const req = route.request()
    if (used || req.method() !== method || !predicate(req)) return route.continue()
    used = true
    if (mode === 'request') captured = { input: req.postDataJSON() }
    else {
      const actual = await route.fetch()
      check([200, 201].includes(actual.status()), 'HELD_REAL_SUCCESS_REQUIRED')
      captured = { response: actual, value: await json(actual), input: (req.headers()['content-type'] || '').startsWith('application/json') ? req.postDataJSON() : null }
    }
    ready(); await releasePromise
    try {
      if (mode === 'request') await route.continue()
      else if (mode === 'abort') await route.abort('failed')
      else await route.fulfill({ response: captured.response })
    } catch { /* A superseded real read may already have been aborted by the app. */ }
    finally { finished() }
  }
  const matcher = url => url.origin === api && url.pathname === path
  await page.route(matcher, handler)
  return { ready: async () => { await bounded(readyPromise); return captured },
    release: async () => { release(); await bounded(finishedPromise); await page.unroute(matcher, handler) } }
}

// The finite normal journeys below operate only on the fixture's one group.
async function settings(page, copy, facts, changes, acknowledge = false) {
  await tab(page, copy, facts, 'settings')
  const form = page.getByRole('heading', { name: copy.network.groups.tabs.settings, exact: true }).locator('..').locator('form')
  for (const [key, value] of Object.entries(changes)) {
    const field = form.getByLabel(copy.network.groups[key], { exact: true })
    if (key === 'description') await field.fill(value)
    else await field.selectOption(value)
  }
  if (acknowledge) await form.getByLabel(copy.network.groups.publication_acknowledge, { exact: true }).check()
  const result = await write(page, groupPath(facts.groupId), () => button(form, copy.network.groups.save_settings).click(), 'PUT')
  check(result.value.operation === 'group_edit', 'REAL_SETTINGS_RECEIPT_REQUIRED')
  await form.locator('fieldset:not([disabled])').waitFor()
  check(await button(form, copy.network.groups.save_settings).isDisabled(), 'SETTINGS_MUST_ADOPT_SAVED_BASELINE')
  return result.value.current
}
async function backIndex(page, copy) {
  await button(page, copy.network.groups.back).click()
  await page.getByRole('heading', { name: copy.network.groups.title, exact: true }).waitFor()
}
async function rosterAction(page, copy, facts, code, action, expectedState, needsConfirm = false) {
  const row = memberRow(page, copy, code)
  const label = ({ invite: 'invite_again', approve: 'approve_member', remove: 'remove', block: 'block', unblock: 'unblock' })[action]
  const click = () => button(row, copy.network.groups[label]).click()
  const result = await write(page, `${groupPath(facts.groupId)}/members/${code}/actions`, () => needsConfirm ? confirm(page, click) : click())
  check(result.value.operation === (action === 'approve' ? 'approve_member' : action) && result.value.current.state === expectedState, 'REAL_ROSTER_TRANSITION_REQUIRED')
  // Wait for the refreshed persisted version, not only the mutation response.
  await row.getByText(new RegExp(quote(copy.network.groups.membership_states[expectedState]) + '$')).waitFor()
  return result.value.current
}
async function ownAction(page, copy, facts, name, action, state, alias, needsConfirm = false) {
  if (alias) await workspace(page, facts).getByLabel(copy.network.groups.member_alias, { exact: true }).fill(alias)
  const click = () => button(workspace(page, facts), copy.network.groups[name]).click()
  const result = await write(page, `${groupPath(facts.groupId)}/membership/actions`, () => needsConfirm ? confirm(page, click) : click())
  check(result.value.operation === action && result.value.current.state === state, 'REAL_OWN_MEMBERSHIP_REQUIRED')
  return result.value.current
}
async function denied(context, path) {
  const response = await request(context, path)
  check([401, 403, 404].includes(response.status()), 'NEXT_REQUEST_ACCESS_DENIAL_REQUIRED'); noStore(response)
  await response.dispose()
}
async function reopen(page, copy, facts) {
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.getByRole('heading', { name: facts.groupName, exact: true, level: 2 }).waitFor()
  return refreshHeader(page, copy, facts)
}

try {
  advance('setup_fixture')
  check(process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === '/browser-fixtures/fixtures.json'
    && JSON.stringify(await readdir('/sys/class/net')) === '["lo"]', 'OWNED_HOSTED_LOOPBACK_REQUIRED')
  check(!Object.keys(process.env).some(key => /^(RESEND_API_KEY|GHL_API_KEY|SUPABASE_SERVICE_KEY|TWILIO_AUTH_TOKEN)$/.test(key)), 'NO_PROVIDER_CREDENTIALS_REQUIRED')
  const stat = await lstat(process.env.ILDR_BROWSER_FIXTURES)
  check(stat.isFile() && !stat.isSymbolicLink() && (stat.mode & 0o777) === 0o600 && stat.size < 100000, 'PRIVATE_FIXTURE_REQUIRED')
  const fixture = JSON.parse(await readFile(process.env.ILDR_BROWSER_FIXTURES, 'utf8'))
  check(fixture.scope === 'social-groups-only', 'EXACT_FIXTURE_SCOPE_REQUIRED')
  const en = JSON.parse(await readFile('/browser-run/en.json', 'utf8')), es = JSON.parse(await readFile('/browser-run/es.json', 'utf8'))
  const facts = { ...fixture.facts }, g = en.network.groups, s = es.network.groups
  advance('setup_browser')
  browser = await chromium.launch({ headless: true })
  advance('setup_owner_session')
  const owner = await session('en', fixture.accounts.owner, 'owner')
  advance('setup_member_session')
  const member = await session('en', fixture.accounts.member, 'buyer')
  advance('setup_professional_session')
  const professional = await session('en', fixture.accounts.professional, 'buyer')
  advance('setup_admin_session')
  const admin = await session('en', fixture.accounts.admin, 'admin')
  const ownerPage = await menuGroups(owner, en, 'owner')

  advance('G01_private_create')
  await button(ownerPage.getByRole('navigation', { name: g.views_label, exact: true }), g.views.create).click()
  const create = ownerPage.getByRole('heading', { name: g.views.create, exact: true }).locator('..').locator('form')
  await create.getByLabel(g.name, { exact: true }).fill(facts.groupName)
  await create.getByLabel(g.description, { exact: true }).fill(facts.description)
  await create.getByLabel(g.area, { exact: true }).fill(facts.areaLabel)
  await create.getByLabel(g.owner_alias, { exact: true }).fill(facts.aliases.owner)
  check(await create.getByLabel(g.visibility, { exact: true }).inputValue() === 'private'
    && await create.getByLabel(g.join_policy, { exact: true }).inputValue() === 'approval_required'
    && await create.getByLabel(g.comments_enabled, { exact: true }).isChecked(), 'PRIVATE_CREATE_DEFAULTS_REQUIRED')
  await geometry(ownerPage, 'en_create', [320])
  let cancelled = false, accidentalCreate = 0
  const createWatch = req => { if (new URL(req.url()).origin === api && new URL(req.url()).pathname === `${prefix}/groups` && req.method() === 'POST') accidentalCreate++ }
  ownerPage.on('request', createWatch)
  ownerPage.once('dialog', async dialog => { cancelled = true; await dialog.dismiss() })
  await button(ownerPage.getByRole('navigation', { name: en.network.main_tabs, exact: true }), en.network.main.projects).click()
  check(cancelled && await create.getByLabel(g.name, { exact: true }).inputValue() === facts.groupName && accidentalCreate === 0, 'DRAFT_CANCEL_MUST_PRESERVE_FORM')
  ownerPage.off('request', createWatch)
  const created = await write(ownerPage, `${prefix}/groups`, () => button(create, g.create).click())
  check(created.value.operation === 'group_create' && created.value.current.visibility === 'private', 'REAL_PRIVATE_CREATE_REQUIRED')
  facts.groupId = created.value.target_id
  await ownerPage.getByRole('heading', { name: facts.groupName, level: 2, exact: true }).waitFor()
  await tab(ownerPage, en, facts, 'settings')
  const imageUpload = await write(ownerPage, `${groupPath(facts.groupId)}/media`, () => ownerPage.getByLabel(g.group_image, { exact: true }).setInputFiles(fixture.files.photo))
  check(imageUpload.value.current.kind === 'group_image', 'ACTUAL_GROUP_IMAGE_REQUIRED')
  const imageId = imageUpload.value.target_id
  await write(ownerPage, groupPath(facts.groupId), () => button(ownerPage, g.save_settings).click(), 'PUT')
  await workspace(ownerPage, facts).locator('header img').waitFor()
  await backIndex(ownerPage, en); await geometry(ownerPage, 'en_index'); await openGroup(ownerPage, en, facts); await geometry(ownerPage, 'en_detail')

  advance('G02_direct_invitation')
  await inviteMember(ownerPage, en, facts, fixture.codes.member)
  const memberPage = await menuGroups(member, en, 'buyer'); await ownInbox(memberPage, en)
  await button(memberPage, g.decline_invite).waitFor()
  const pendingHeader = await request(member, groupPath(facts.groupId))
  check(pendingHeader.status() === 200 && (await json(pendingHeader)).image === null, 'INVITEE_HEADER_REDACTION_REQUIRED'); await pendingHeader.dispose()
  await denied(member, `${prefix}/group-media/${imageId}`)
  const declined = await write(memberPage, `${groupPath(facts.groupId)}/membership/actions`, () => button(memberPage, g.decline_invite).click())
  check(declined.value.current.state === 'declined', 'DECLINED_INVITATION_REQUIRED')
  await refreshHeader(ownerPage, en, facts)
  await tab(ownerPage, en, facts, 'members')
  await button(ownerPage, en.network.refresh).last().click()
  await rosterAction(ownerPage, en, facts, fixture.codes.member, 'invite', 'invited')
  await memberPage.reload({ waitUntil: 'domcontentloaded' }); await ownInbox(memberPage, en)
  await acceptInbox(memberPage, en, facts, facts.aliases.member)
  await button(memberPage, g.open).click(); await refreshHeader(memberPage, en, facts)
  await refreshHeader(ownerPage, en, facts); await tab(ownerPage, en, facts, 'members')
  await button(ownerPage, en.network.refresh).last().click()
  await changeRole(ownerPage, en, facts, fixture.codes.member, 'moderator'); await geometry(ownerPage, 'en_members', [320])

  advance('G05_fragment_invitation')
  await tab(ownerPage, en, facts, 'links')
  const links = []
  for (let index = 0; index < 2; index++) {
    const made = await write(ownerPage, `${groupPath(facts.groupId)}/invite-links`, () => button(ownerPage, g.create_link).click())
    check(typeof made.input.token === 'string' && made.input.token.length === 43, 'CLIENT_GENERATED_FRAGMENT_TOKEN_REQUIRED')
    await button(ownerPage, g.copy_link).click()
    await ownerPage.getByText(g.link_copied, { exact: true }).or(ownerPage.getByLabel(g.manual_copy, { exact: true })).waitFor()
    let copied
    if (await ownerPage.getByLabel(g.manual_copy, { exact: true }).count()) copied = await ownerPage.getByLabel(g.manual_copy, { exact: true }).inputValue()
    else copied = await ownerPage.evaluate(() => navigator.clipboard.readText())
    check(copied === `${dashboard}/group-invite/#token=${made.input.token}`, 'MANUAL_FRAGMENT_COPY_REQUIRED')
    links.push({ id: made.value.target_id, token: made.input.token })
    await button(ownerPage, g.done_copying).click()
  }
  const outsider = await session('en'), invitePage = await outsider.newPage(); invitePages.add(invitePage)
  await invitePage.goto(`${dashboard}/group-invite/?token=ignored#token=${links[0].token}`, { waitUntil: 'domcontentloaded' })
  await invitePage.getByRole('heading', { name: g.invite_title, exact: true }).waitFor()
  await button(invitePage, g.check_signin).waitFor({ state: 'visible' })
  check(new URL(invitePage.url()).hash === '' && new URL(invitePage.url()).search === '', 'INVITE_URL_SCRUB_REQUIRED')
  const signin = invitePage.getByRole('link', { name: g.signin, exact: true })
  await signin.waitFor()
  check(await signin.getAttribute('href') === landing + '/login/' && await signin.getAttribute('target') === '_blank'
    && /noreferrer/.test(await signin.getAttribute('rel') || ''), 'TOKEN_FREE_SEPARATE_SIGNIN_REQUIRED')
  await geometry(invitePage, 'invite_shell', [320])
  const [signinPage, signinRequest] = await Promise.all([outsider.waitForEvent('page'),
    outsider.waitForEvent('request', req => req.isNavigationRequest() && req.url() === landing + '/login/'), signin.click()])
  // Landing is unchanged and intentionally outside this no-Next-repeat rehearsal.
  check(signinRequest.url() === landing + '/login/' && !signinRequest.url().includes(links[0].token), 'SIGNIN_DESTINATION_TOKEN_FREE_REQUIRED'); await signinPage.close()
  await login(outsider, fixture.accounts.outsider, 'realtor')
  await button(invitePage, g.check_signin).click(); await button(invitePage, g.preview_invite).waitFor()
  const firstResolve = await hold(invitePage, `${prefix}/group-invites/resolve`, 'POST', req => req.postDataJSON().token === links[0].token)
  await button(invitePage, g.preview_invite).click(); await firstResolve.ready()
  await invitePage.evaluate(token => { location.hash = 'token=' + token }, links[1].token)
  await invitePage.waitForFunction(() => location.hash === '' && location.search === '')
  await button(invitePage, g.preview_invite).waitFor({ state: 'visible' })
  await firstResolve.release()
  check(await invitePage.getByRole('heading', { name: facts.groupName, exact: true }).count() === 0
    && new URL(invitePage.url()).hash === '', 'SUPERSEDED_PREVIEW_MUST_NOT_REAPPEAR')
  await write(invitePage, `${prefix}/group-invites/resolve`, () => button(invitePage, g.preview_invite).click())
  await invitePage.getByLabel(g.member_alias, { exact: true }).fill(facts.aliases.outsider)
  const oldAccept = await hold(invitePage, `${prefix}/group-invites/accept`, 'POST', req => req.postDataJSON().token === links[1].token)
  humanAcceptBudget = 1
  let accepted
  try {
    const accepting = write(invitePage, `${prefix}/group-invites/accept`, () => button(invitePage, g.accept_link).click())
    await oldAccept.ready()
    await invitePage.evaluate(token => { location.hash = 'token=' + token }, links[0].token)
    await invitePage.waitForFunction(() => location.hash === '' && location.search === '')
    await button(invitePage, g.preview_invite).waitFor()
    await oldAccept.release(); accepted = await accepting
  }
  finally { humanAcceptBudget = 0 }
  check(accepted.value.current.state === 'active' && linkAcceptRequests === 1, 'HUMAN_FRAGMENT_ACCEPTANCE_REQUIRED')
  check(await invitePage.getByText(g.invite_saved, { exact: true }).count() === 0, 'SUPERSEDED_ACCEPT_MUST_NOT_CLEAR_NEXT_INVITE')
  const currentPreview = await write(invitePage, `${prefix}/group-invites/resolve`, () => button(invitePage, g.preview_invite).click())
  check(currentPreview.value.already_member === true && currentPreview.input.token === links[0].token, 'SECOND_INVITE_MUST_REMAIN_CURRENT')
  invitePages.delete(invitePage)
  await invitePage.getByRole('link', { name: g.open, exact: true }).click()
  const outsiderPage = invitePage
  await outsiderPage.getByRole('heading', { name: facts.groupName, level: 2, exact: true }).waitFor()
  await tab(ownerPage, en, facts, 'links')
  // The service orders links by creation time descending, and the UI maps that order.
  const latestLinks = await request(owner, `${groupPath(facts.groupId)}/invite-links?page=1`)
  check(latestLinks.status() === 200 && (await json(latestLinks)).items[0]?.id === links[1].id, 'LATEST_LINK_TARGET_REQUIRED')
  await latestLinks.dispose()
  const revokedB = await write(ownerPage, `${groupPath(facts.groupId)}/invite-links/${links[1].id}/revoke`,
    () => confirm(ownerPage, () => button(ownerPage.locator('article').filter({ has: button(ownerPage, g.revoke_link) }).first(), g.revoke_link).click()))
  check(revokedB.value.current.state === 'revoked', 'NORMAL_LINK_REVOCATION_REQUIRED')

  advance('G03_private_photos_comments')
  const privateEntry = await createPost(outsiderPage, en, facts, facts.privatePost, fixture.files.photo)
  check(privateEntry.post.created_audience === 'private' && privateEntry.post.media_ids.includes(privateEntry.media.id), 'IMMUTABLE_PRIVATE_POST_REQUIRED')
  await refreshHeader(memberPage, en, facts); await tab(memberPage, en, facts, 'feed')
  await refreshFeed(memberPage, en, facts)
  const privateCard = postCard(memberPage, facts.privatePost)
  await button(privateCard, en.network.comments).click()
  await privateCard.getByLabel(en.network.comment_context, { exact: true }).selectOption(privateEntry.media.id)
  await privateCard.getByLabel(en.network.write_update, { exact: true }).fill(facts.comment)
  const comment = await write(memberPage, `${prefix}/group-posts/${privateEntry.post.id}/comments`, () => button(privateCard, en.network.save_entry).click())
  check(comment.value.current.media_id === privateEntry.media.id, 'PHOTO_SPECIFIC_COMMENT_REQUIRED')
  await privateCard.getByText(facts.comment, { exact: true }).waitFor()

  advance('G12_uncertain_auth_loss')
  await tab(ownerPage, en, facts, 'feed')
  const uncertainUpload = await hold(ownerPage, `${groupPath(facts.groupId)}/media`, 'POST', () => true, 'abort')
  await ownerPage.locator('form input[type=file]').setInputFiles(fixture.files.photo)
  const actualUpload = await uncertainUpload.ready()
  check(actualUpload.value.current.kind === 'post', 'REAL_UNCERTAIN_UPLOAD_REQUIRED')
  await uncertainUpload.release()
  await button(ownerPage, g.check_action).waitFor()
  check(await button(ownerPage, g.retry_same).count() === 0, 'UNCERTAIN_UPLOAD_MUST_NOT_RESEND')
  const oldRead = await hold(ownerPage, `${groupPath(facts.groupId)}/posts`, 'GET', () => true)
  await button(ownerPage.getByRole('heading', { name: g.feed_title, exact: true }).locator('..'), en.network.refresh).first().click(); await oldRead.ready()
  await owner.clearCookies()
  const denialPromise = ownerPage.waitForResponse(response => pathIs(response, groupPath(facts.groupId), 'GET') && [401, 403].includes(response.status()))
  const probePromise = ownerPage.waitForResponse(response => new URL(response.url()).origin === api
    && new URL(response.url()).pathname === `${prefix}/group-submissions/${actualUpload.value.submission_id}` && [401, 403].includes(response.status()))
  await button(ownerPage, g.check_action).click(); await probePromise; await denialPromise
  await oldRead.release()
  await ownerPage.getByRole('heading', { name: facts.groupName, exact: true }).waitFor({ state: 'hidden' })
  check(await ownerPage.getByText(facts.privatePost, { exact: true }).count() === 0 && await ownerPage.locator('img[src^="blob:"]').count() === 0, 'AUTH_LOSS_MUST_CLEAR_PRIVATE_SCOPE')
  await login(owner, fixture.accounts.owner, 'owner')
  await ownerPage.reload({ waitUntil: 'domcontentloaded' }); await ownerPage.getByRole('heading', { name: facts.groupName, level: 2, exact: true }).waitFor()

  advance('G07_publication_draft_version')
  await tab(ownerPage, en, facts, 'feed')
  const draftForm = ownerPage.getByRole('heading', { name: g.feed_title, exact: true }).locator('..').locator('form').first()
  await draftForm.getByLabel(g.write_post, { exact: true }).fill(facts.reply)
  const draft = await hold(ownerPage, `${groupPath(facts.groupId)}/posts`, 'POST', () => true, 'request')
  await button(draftForm, g.publish_post).click(); const capturedDraft = await draft.ready()
  const peer = await session('en', null, null, await owner.storageState())
  const peerPage = await menuGroups(peer, en, 'owner')
  await switchLanguage(peerPage, en)
  await peerPage.getByRole('heading', { name: s.title, exact: true }).waitFor()
  await geometry(peerPage, 'es_index'); await openGroup(peerPage, es, facts); await geometry(peerPage, 'es_detail')
  await tab(peerPage, es, facts, 'settings')
  await peerPage.getByLabel(s.visibility, { exact: true }).selectOption('network')
  await peerPage.getByLabel(s.description, { exact: true }).fill(facts.editedDescription)
  await geometry(peerPage, 'es_publication', [320])
  check(await button(peerPage, s.save_settings).isDisabled(), 'EXPLICIT_PUBLICATION_REVIEW_REQUIRED')
  await peerPage.getByLabel(s.publication_acknowledge, { exact: true }).check()
  const published = await write(peerPage, groupPath(facts.groupId), () => button(peerPage, s.save_settings).click(), 'PUT')
  check(published.value.current.visibility === 'network' && published.value.current.version > capturedDraft.input.expected_group_version, 'REVIEWED_PUBLICATION_VERSION_REQUIRED')
  await peerPage.getByRole('heading', { name: s.tabs.settings, exact: true }).locator('..').locator('fieldset:not([disabled])').waitFor()
  const stale = ownerPage.waitForResponse(response => pathIs(response, `${groupPath(facts.groupId)}/posts`, 'POST') && response.status() === 409)
  await draft.release(); await stale
  const absent = await request(owner, `${prefix}/group-submissions/${capturedDraft.input.submission_id}`)
  check(absent.status() === 404, 'STALE_PRIVATE_DRAFT_MUST_HAVE_NO_RECEIPT'); await absent.dispose()
  await workspace(ownerPage, facts).locator('header').getByText(new RegExp('^' + quote(g.visibilities.network) + ' ·')).waitFor()
  await ownerPage.getByLabel(g.write_post, { exact: true }).waitFor()
  check(await ownerPage.getByLabel(g.write_post, { exact: true }).inputValue() === '', 'PRIVATE_DRAFT_MUST_NOT_BECOME_NETWORK_DRAFT')
  await tab(peerPage, es, facts, 'feed')
  const networkEntry = await createPost(peerPage, es, facts, facts.networkPost)
  check(networkEntry.post.created_audience === 'network', 'EXPLICIT_NEW_NETWORK_POST_REQUIRED')

  advance('G06_discovery_requests')
  const professionalPage = await menuGroups(professional, en, 'buyer')
  await button(professionalPage.getByRole('navigation', { name: g.views_label, exact: true }), g.views.discover).click()
  await openGroup(professionalPage, en, facts)
  await postCard(professionalPage, facts.networkPost).waitFor()
  check(await professionalPage.getByText(facts.privatePost, { exact: true }).count() === 0, 'PRIVATE_AUDIENCE_MUST_STAY_PRIVATE')
  await denied(professional, `${prefix}/group-media/${privateEntry.media.id}`)
  await ownAction(professionalPage, en, facts, 'request', 'request', 'requested', facts.aliases.professional)
  await ownAction(professionalPage, en, facts, 'cancel_request', 'cancel', 'cancelled')
  await refreshHeader(professionalPage, en, facts)
  await ownAction(professionalPage, en, facts, 'request', 'request', 'requested', facts.aliases.professional)
  await refreshHeader(peerPage, es, facts); await tab(peerPage, es, facts, 'members')
  await rosterAction(peerPage, es, facts, fixture.codes.professional, 'approve', 'active')
  await changeRole(peerPage, es, facts, fixture.codes.professional, 'admin')

  advance('G04_roles_moderation')
  await settings(peerPage, es, facts, { post_policy: 'moderated' })
  await refreshHeader(outsiderPage, en, facts); await tab(outsiderPage, en, facts, 'feed')
  const moderated = await createPost(outsiderPage, en, facts, facts.moderatedPost)
  check(moderated.post.status === 'pending', 'ORDINARY_MEMBER_PENDING_POST_REQUIRED')
  await refreshHeader(memberPage, en, facts); await tab(memberPage, en, facts, 'feed')
  await refreshFeed(memberPage, en, facts)
  const moderatedCard = postCard(memberPage, facts.moderatedPost)
  const approved = await write(memberPage, `${prefix}/group-posts/${moderated.post.id}/actions`, () => button(moderatedCard, en.network.approve_post).click())
  check(approved.value.current.status === 'visible', 'ACTUAL_MODERATOR_APPROVAL_REQUIRED')
  await moderatedCard.getByText(new RegExp('^' + quote(en.network.post_statuses.visible) + ' ·')).waitFor()
  const hidden = await write(memberPage, `${prefix}/group-posts/${moderated.post.id}/actions`, () => confirm(memberPage, () => button(moderatedCard, en.network.hide_post).click()))
  check(hidden.value.current.status === 'hidden', 'ACTUAL_MODERATOR_HIDE_REQUIRED')

  advance('G10_history_unblock')
  await refreshHeader(peerPage, es, facts); await tab(peerPage, es, facts, 'members')
  await rosterAction(peerPage, es, facts, fixture.codes.outsider, 'block', 'blocked', true)
  await denied(outsider, `${prefix}/group-media/${privateEntry.media.id}`)
  await peerPage.reload({ waitUntil: 'domcontentloaded' }); await tab(peerPage, es, facts, 'members')
  await peerPage.getByLabel(s.roster_filter, { exact: true }).selectOption('all')
  await rosterAction(peerPage, es, facts, fixture.codes.outsider, 'unblock', 'removed')
  await rosterAction(peerPage, es, facts, fixture.codes.outsider, 'invite', 'invited')
  await outsiderPage.goto(`${dashboard}/property-network?workspace=realtor&tab=groups`, { waitUntil: 'domcontentloaded' })
  await ownInbox(outsiderPage, en); await acceptInbox(outsiderPage, en, facts, facts.aliases.outsider)
  await button(outsiderPage, g.open).click(); await refreshHeader(outsiderPage, en, facts)

  advance('G08_open_join_leave_scope')
  await settings(peerPage, es, facts, { join_policy: 'open' })
  await refreshHeader(outsiderPage, en, facts)
  await ownAction(outsiderPage, en, facts, 'leave', 'leave', 'left', null, true)
  await refreshHeader(outsiderPage, en, facts)
  await ownAction(outsiderPage, en, facts, 'join', 'join', 'active', facts.aliases.outsider)
  for (const path of [`${prefix}/registry/${facts.unrelatedPropertyId}`, `${prefix}/projects/${facts.unrelatedProjectId}`]) {
    const authorized = await request(owner, path)
    check(authorized.status() === 200, 'ACTUAL_OWNED_PRIVATE_RECORD_REQUIRED'); noStore(authorized); await authorized.dispose()
    for (const actor of [outsider, professional]) await denied(actor, path)
  }
  await ownAction(outsiderPage, en, facts, 'leave', 'leave', 'left', null, true)
  await denied(outsider, `${prefix}/group-media/${privateEntry.media.id}`)

  advance('G09_withdrawal')
  const withdrawal = outsiderPage.waitForResponse(response => pathIs(response, groupPath(facts.groupId), 'GET') && response.status() === 404)
  await settings(peerPage, es, facts, { visibility: 'private' })
  await denied(outsider, groupPath(facts.groupId)); await denied(outsider, `${prefix}/group-media/${imageId}`)
  await outsiderPage.bringToFront(); await outsiderPage.evaluate(() => window.dispatchEvent(new Event('focus')))
  await withdrawal
  await outsiderPage.getByText(facts.networkPost, { exact: true }).waitFor({ state: 'hidden' })
  await tab(peerPage, es, facts, 'members')
  await rosterAction(peerPage, es, facts, fixture.codes.outsider, 'invite', 'invited')

  advance('G11_archive_revoke')
  const archiveSection = peerPage.getByRole('heading', { name: s.archive_title, exact: true }).locator('..')
  await archiveSection.getByLabel(s.reason, { exact: true }).fill(facts.reason)
  const archived = await write(peerPage, `${groupPath(facts.groupId)}/actions`, () => confirm(peerPage, () => button(archiveSection, s.archive).click()))
  check(archived.value.current.status === 'archived' && archived.value.current.visibility === 'private', 'ARCHIVED_PRIVATE_FINAL_GROUP_REQUIRED')
  await refreshHeader(professionalPage, en, facts)
  const closed = await refreshHeader(professionalPage, en, facts)
  check(!closed.can_manage && !closed.can_post && closed.can_revoke_access && closed.can_revoke_links, 'CLOSED_MANAGER_REVOCATION_CAPABILITIES_REQUIRED')
  await tab(professionalPage, en, facts, 'members')
  await changeRole(professionalPage, en, facts, fixture.codes.member, 'member')
  await rosterAction(professionalPage, en, facts, fixture.codes.member, 'remove', 'removed', true)
  await denied(member, groupPath(facts.groupId)); await denied(member, `${prefix}/group-media/${privateEntry.media.id}`)
  await tab(professionalPage, en, facts, 'links')
  const revokedA = await write(professionalPage, `${groupPath(facts.groupId)}/invite-links/${links[0].id}/revoke`,
    () => confirm(professionalPage, () => button(professionalPage, g.revoke_link).click()))
  check(revokedA.value.current.state === 'revoked', 'CLOSED_LINK_REVOCATION_REQUIRED')
  await outsiderPage.goto(`${dashboard}/property-network?workspace=realtor&tab=groups`, { waitUntil: 'domcontentloaded' })
  await ownInbox(outsiderPage, en)
  const finalDecline = await write(outsiderPage, `${groupPath(facts.groupId)}/membership/actions`, () => button(outsiderPage, g.decline_invite).click())
  check(finalDecline.value.current.state === 'declined', 'CLOSED_OWN_DECLINE_REQUIRED')
  const oversight = await request(admin, groupPath(facts.groupId))
  check(oversight.status() === 200 && !(await json(oversight)).can_post, 'ADMIN_OVERSIGHT_IS_NOT_MEMBERSHIP'); await oversight.dispose()
  check(geometries.length === 16 && pageErrors === 0 && externalWrites === 0 && trackingRequests === 0
    && automaticAccepts === 0 && tokenLeaks === 0 && inviteProviders === 0, 'EXACT_PRIVACY_AND_GEOMETRY_REQUIRED')
  clearTimeout(timer)
  console.log('SOCIAL_GROUPS_BROWSER_RESULT ' + JSON.stringify({ status: 'PASS', checks, geometry_checks: 16,
    locales: ['en', 'es'], page_errors: pageErrors, external_writes: externalWrites, tracking_requests: trackingRequests,
    automatic_accepts: automaticAccepts, token_leaks: tokenLeaks }))
} catch (cause) {
  if (entryInProgress) await entryDiagnostic(cause)
  console.log('SOCIAL_GROUPS_BROWSER_FAILURE ' + stage + ' ' + (cause instanceof Failure ? cause.message : 'LOCATOR_OR_EVENT_DEADLINE'))
  process.exitCode = 1
} finally {
  clearTimeout(timer)
  for (const context of contexts) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
}
