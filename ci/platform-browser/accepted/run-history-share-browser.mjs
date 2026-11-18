import { lstat, readFile } from 'node:fs/promises'
import { isDeepStrictEqual } from 'node:util'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const base = '/property-network/history-shares'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const copy = {
  en: {
    mainNav: 'Property network sections', historyNav: 'History shares', list: 'Property history shares', detail: 'Property history share',
    history: 'Property service history', create: 'Share property history', received: 'Received', open: 'Open history share',
    back: 'Back to history shares', pending: 'Awaiting acceptance', accepted: 'Accepted', summaries: 'Shared work summaries',
    accept: 'Accept invitation', confirm: 'Confirm action', refresh: 'Refresh', empty: 'You have no current history invitations or shares.',
    unavailable: 'This record is unavailable or your current account no longer has access.',
  },
  es: {
    mainNav: 'Secciones de la red de propiedades', historyNav: 'Historial compartido', list: 'Historial compartido de propiedades',
    detail: 'Historial compartido de la propiedad', history: 'Historial de servicios', create: 'Compartir historial de la propiedad',
    chooseAnchor: 'Elige un proyecto activo', recipient: 'Profesional', label: 'Título de la invitación', expiry: 'Vence después de',
    summary: 'Resumen del registro seleccionado 1', reviewed: 'Revisé el título de la invitación y cada resumen y eliminé los datos privados.',
    submit: 'Crear invitación al historial', viewSent: 'Ver historiales enviados', sent: 'Enviados', open: 'Abrir historial compartido',
    confirm: 'Confirmar acción', revoke: 'Revocar acceso', revoked: 'Revocado', team: 'Equipo del proyecto',
    memberRevoke: 'Revocar acceso al proyecto', memberCode: 'Código de miembro', memberRole: 'Rol en el proyecto',
    memberGrant: 'Conceder acceso al proyecto',
  },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_property_history_shares', checks: [], stage_writes: 0,
  action_writes: 0, membership_writes: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = new Set()
const shareIds = new Set()
let stage = 'fixture'
let externalNavigation = false

class RehearsalFailure extends Error {}
function require(condition, code) { if (!condition) throw new RehearsalFailure(code) }
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const date = /^\d{4}-\d{2}-\d{2}$/
function keys(value, names) {
  return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === names.length
    && names.every(name => Object.hasOwn(value, name))
}

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(keys(value, ['accounts', 'historyShare']) && keys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(keys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const f = value.historyShare
  require(keys(f, ['private', 'listing', 'winnerUserCode', 'otherProfessionalUserCode', 'managerUserCode',
    'listingAnchorVersion', 'expiresInDays', 'asOf', 'expiresAt']) && f.expiresInDays === 7 && date.test(f.asOf)
    && Number.isFinite(Date.parse(f.expiresAt)) && Date.parse(f.expiresAt) > Date.now()
    && [f.winnerUserCode, f.otherProfessionalUserCode, f.managerUserCode, f.listingAnchorVersion].every(n => Number.isInteger(n) && n > 0)
    && new Set([f.winnerUserCode, f.otherProfessionalUserCode, f.managerUserCode]).size === 3, 'EXACT_HISTORY_FIXTURE_SCOPE_REQUIRED')
  const identities = new Set()
  for (const kind of ['private', 'listing']) {
    const source = f[kind]
    require(keys(source, ['sourceKind', 'sourceId', 'sourceTitle', 'anchorProjectId', 'anchorProjectTitle', 'historyProjectId',
      'historyProjectTitle', 'selectedUpdateId', 'unselectedUpdateId', 'selectedOn', 'categoryIds', 'shareLabel', 'sharedSummary', 'rawBody'])
      && source.sourceKind === kind && source.shareLabel === `Synthetic ${kind} pool history`
      && source.sharedSummary === `Synthetic redacted ${kind} pool maintenance summary.`
      && source.rawBody === `Synthetic ${kind} internal note, withheld from the share.`
      && date.test(source.selectedOn) && source.selectedOn < f.asOf && isDeepStrictEqual(source.categoryIds, ['pool_service']),
    'EXACT_SYNTHETIC_HISTORY_SOURCE_REQUIRED')
    for (const identity of [source.sourceId, source.anchorProjectId, source.historyProjectId, source.selectedUpdateId, source.unselectedUpdateId]) {
      require(uuid.test(identity) && !identities.has(identity), 'DISTINCT_SYNTHETIC_HISTORY_IDENTITIES_REQUIRED')
      identities.add(identity)
    }
  }
  return value
}

const projectPath = id => `/property-network/projects/${id}`
const detailPath = id => `${base}/${id}`
const actionPath = id => `${detailPath(id)}/actions`
const receiptPath = id => `${base}/submissions/${id}`
const optionsPath = source => `${base}/options/${source.sourceKind}/${source.sourceId}`
const optionsUrl = (source, anchor = source.anchorProjectId) => `${optionsPath(source)}?anchor_project_id=${anchor}&page=1`
const listPath = view => `${base}?view=${view}&page=1`
const membersPath = f => `${projectPath(f.listing.anchorProjectId)}/members`
const memberRevokePath = f => `${membersPath(f)}/${f.winnerUserCode}/revoke`

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}
function privateHeaders(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache' && headers['x-content-type-options'] === 'nosniff'
}

async function read(state, path, status = 200) {
  const response = await state.context.request.get(`${api}${path}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(response.status() === status && privateHeaders(response), 'PRIVATE_READ_STATUS_OR_HEADERS')
    const body = await response.json()
    if (status >= 400) require(keys(body, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return body
  } finally { await response.dispose() }
}

function classifyWrite(url, method, role, f) {
  if (url.origin !== api || url.search || method !== 'POST') return null
  if (role === 'owner' && url.pathname === base) return 'stage_writes'
  if (['owner', 'winner'].includes(role) && [...shareIds].some(id => url.pathname === actionPath(id))) return 'action_writes'
  if (role === 'owner' && [membersPath(f), memberRevokePath(f)].includes(url.pathname)) return 'membership_writes'
  return null
}

async function session(browser, fixture, role, lang = 'en') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    timezoneId: lang === 'es' ? 'Asia/Tokyo' : 'America/Los_Angeles', locale: lang === 'es' ? 'es-DO' : 'en-US' })
  contexts.add(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, lang }) => {
    if (location.origin !== dashboard) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (![dashboard, api].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) externalNavigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      const counter = classifyWrite(url, request.method(), role, fixture.historyShare)
      if (!counter) {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
      result[counter] += 1
    }
    return route.continue()
  })
  const account = fixture.accounts[role]
  if (account) {
    const login = await context.request.post(`${api}/auth/login`, { data: account, headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
      require(login.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'REAL_SYNTHETIC_AUTH_REQUIRED')
    } finally { await login.dispose() }
  }
  return { context, page: account ? await context.newPage() : null, role, account, t: copy[lang], lang }
}

async function close(state) {
  await state.context.close()
  contexts.delete(state.context)
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

async function navigate(state, path) {
  const { page, role, account } = state
  await page.bringToFront()
  const [auth] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}${path}`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await auth.json()
  require(auth.status() === 200 && user.role === roles[role] && user.email === account.email, 'SIGNED_IN_ROLE_MISMATCH')
  await foreground(page)
  require(await page.evaluate(lang => localStorage.getItem('ildr_lang') === lang && !document.cookie.includes('ildr_token='), state.lang),
    'ISOLATED_BROWSER_CONTEXT_INVALID')
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const heading = (scope, name, level) => scope.getByRole('heading', { name, exact: true, level })
const region = (state, name) => state.page.getByRole('region', { name, exact: true })
const listPanel = state => region(state, state.t.list)
const detailPanel = state => region(state, state.t.detail)

async function mobileFits(locator) {
  await locator.scrollIntoViewIfNeeded()
  require(await locator.evaluate(element => {
    const box = element.getBoundingClientRect()
    return innerWidth === 390 && innerHeight === 844 && box.left >= -1 && box.right <= innerWidth + 1
      && document.documentElement.scrollWidth <= innerWidth + 1
  }), 'MOBILE_HORIZONTAL_OVERFLOW')
}

async function openProject(state, source) {
  await navigate(state, `/property-network?tab=projects&project=${source.anchorProjectId}`)
  await heading(state.page, source.anchorProjectTitle, 1).waitFor()
}

async function openProjectHistory(state, source) {
  await openProject(state, source)
  await button(state.page, state.t.history).click()
}

async function expectProjectDenied(state, source) {
  await read(state, projectPath(source.historyProjectId), 404)
  await read(state, `${projectPath(source.historyProjectId)}/updates?page=1`, 404)
}

async function openPicker(owner, source) {
  if (source.sourceKind === 'private') {
    await navigate(owner, `/property-network?tab=properties&property=${source.sourceId}`)
    await heading(owner.page, source.sourceTitle, 1).waitFor()
  } else await openProjectHistory(owner, source)
  await button(owner.page, owner.t.create).click()
  const panel = region(owner, owner.t.create)
  const anchor = panel.getByRole('listitem').filter({ has: owner.page.getByText(source.anchorProjectTitle, { exact: true }) })
  await button(anchor, owner.t.chooseAnchor).click()
  await panel.getByLabel(owner.t.recipient, { exact: true }).waitFor()
  await mobileFits(panel)
  return panel
}

async function assertPicker(state, source, f) {
  const data = await read(state, optionsUrl(source))
  require(keys(data, ['source_kind', 'source_id', 'anchor_project_id', 'professionals', 'items', 'total', 'page', 'page_size'])
    && data.source_kind === source.sourceKind && data.source_id === source.sourceId && data.anchor_project_id === source.anchorProjectId
    && data.total === 2 && data.page === 1 && data.page_size === 30 && Array.isArray(data.professionals)
    && data.professionals.length === 1 && keys(data.professionals[0], ['user_code', 'display_name'])
    && data.professionals[0].user_code === f.winnerUserCode && Array.isArray(data.items) && data.items.length === 2,
  'CURRENT_OWNER_PICKER_SCOPE_REQUIRED')
  for (const [index, item] of data.items.entries()) require(keys(item, ['update_id', 'project_title', 'occurred_on', 'service_category_ids'])
    && item.update_id === [source.selectedUpdateId, source.unselectedUpdateId][index] && item.project_title === source.historyProjectTitle
    && isDeepStrictEqual(item.service_category_ids, source.categoryIds) && (index !== 0 || item.occurred_on === source.selectedOn),
  'PICKER_EXPOSED_OTHER_PROJECT_OR_RAW_HISTORY')
}

function assertReceipt(value, id, operation, status, version) {
  require(keys(value, ['id', 'operation', 'status', 'version']) && uuid.test(value.id) && (!id || value.id === id)
    && value.operation === operation && value.status === status && value.version === version, 'EXACT_MINIMAL_RECEIPT_REQUIRED')
  return value.id
}

async function mutation(state, path, target, expected, status, useKey = true) {
  const [response] = await Promise.all([
    state.page.waitForResponse(response => endpoint(response, path, 'POST')),
    target.click(),
  ])
  require(response.status() === status && privateHeaders(response), 'EXACT_PRIVATE_MUTATION_FAILED')
  const body = response.request().postDataJSON()
  const { submission_id: key, ...payload } = body
  require(isDeepStrictEqual(payload, expected) && (useKey ? uuid.test(key) : !Object.hasOwn(body, 'submission_id')),
    'EXACT_SYNTHETIC_MUTATION_BODY_REQUIRED')
  return { value: await response.json(), key }
}

async function createShare(owner, source, f, existingPanel) {
  const panel = existingPanel ?? await openPicker(owner, source)
  await foreground(owner.page)
  const recipient = panel.getByLabel(owner.t.recipient, { exact: true })
  await recipient.selectOption(String(f.winnerUserCode))
  require(await recipient.locator('option').count() === 2, 'DRAFT_OFFERED_AN_UNASSIGNED_PROFESSIONAL')
  await panel.getByLabel(owner.t.label, { exact: true }).fill(source.shareLabel)
  await panel.getByLabel(owner.t.expiry, { exact: true }).selectOption(String(f.expiresInDays))
  const option = panel.getByRole('checkbox', { name: `Seleccionar registro 1 de ${source.historyProjectTitle}`, exact: true })
  require(await option.locator('xpath=..').locator(`time[datetime="${source.selectedOn}"]`).count() === 1,
    'DRAFT_SELECTED_THE_WRONG_HISTORY_DATE')
  await option.check()
  const summary = panel.locator('textarea')
  await summary.waitFor()
  require(await summary.count() === 1 && await summary.evaluate((field, name) => [...field.labels].some(label => [...label.childNodes]
    .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() === name), owner.t.summary)
    && await summary.inputValue() === '', 'DRAFT_MUST_NOT_COPY_ORIGINAL_HISTORY_TEXT')
  await summary.fill(source.sharedSummary)
  const submit = button(panel, owner.t.submit)
  require(await submit.isDisabled(), 'PRIVATE_REDACTION_REVIEW_MUST_BE_EXPLICIT')
  await panel.getByRole('checkbox', { name: owner.t.reviewed, exact: true }).check()
  const staged = await mutation(owner, base, submit, { source_kind: source.sourceKind, source_id: source.sourceId,
    anchor_project_id: source.anchorProjectId, recipient_user_code: f.winnerUserCode, label: source.shareLabel,
    expires_in_days: f.expiresInDays, items: [{ update_id: source.selectedUpdateId, summary: source.sharedSummary }] }, 201)
  const id = assertReceipt(staged.value, null, 'create', 'pending', 1)
  shareIds.add(id)
  await button(panel, owner.t.viewSent).click()
  await button(listPanel(owner), owner.t.sent).waitFor()
  return { id, key: staged.key }
}

async function openInbox(state) {
  await navigate(state, '/property-network')
  const nav = state.page.getByRole('navigation', { name: state.t.mainNav, exact: true })
  await button(nav, state.t.historyNav).click()
  await listPanel(state).waitFor()
  if (state.t.received) await button(listPanel(state), state.t.received).waitFor()
}

async function openShare(state, source) {
  await foreground(state.page)
  const row = listPanel(state).getByRole('listitem').filter({ has: heading(state.page, source.shareLabel, 3) })
  await button(row, state.t.open).click()
  const detail = detailPanel(state)
  await heading(detail, source.shareLabel, 3).waitFor()
  await mobileFits(detail)
  return detail
}

function assertShare(row, id, source, f, view, status, details) {
  require(keys(row, ['id', 'label', 'view', 'status', 'expires_at', 'version', 'item_count', 'items', 'actions'])
    && row.id === id && row.label === source.shareLabel && row.view === view && row.status === status
    && Date.parse(row.expires_at) === Date.parse(f.expiresAt) && row.item_count === 1
    && row.version === { pending: 1, accepted: 2, revoked: 3 }[status], 'EXACT_SHARE_METADATA_REQUIRED')
  const expected = status === 'revoked' ? [] : status === 'pending' && view === 'received' ? ['accept', 'decline'] : ['revoke']
  require(isDeepStrictEqual(row.actions, expected) && isDeepStrictEqual(row.items, details ? [{ occurred_on: source.selectedOn,
    service_category_ids: source.categoryIds, summary: source.sharedSummary }] : null), 'PRIVATE_HISTORY_TEXT_OR_FIELDS_LEAKED')
}

async function assertListed(state, id, source, f, view, status) {
  const body = await read(state, listPath(view))
  require(keys(body, ['view', 'items', 'total', 'page', 'page_size']) && body.view === view && body.page === 1 && body.page_size === 30
    && Array.isArray(body.items), 'EXACT_PRIVATE_LIST_REQUIRED')
  assertShare(body.items.find(row => row.id === id), id, source, f, view, status, false)
}

async function assertAbsent(state, id, view) {
  const body = await read(state, listPath(view))
  require(Array.isArray(body.items) && !body.items.some(row => row.id === id), 'CLOSED_HISTORY_SHARE_STILL_LISTED')
}

async function action(state, id, name, operation, version, status) {
  await foreground(state.page)
  const panel = detailPanel(state)
  await button(panel, name).click()
  const written = await mutation(state, actionPath(id), button(panel, state.t.confirm), { operation, expected_version: version }, 200)
  assertReceipt(written.value, id, operation, status, version + 1)
  return written.key
}

async function verifyVisibleSummary(winner, source) {
  await foreground(winner.page)
  const panel = detailPanel(winner), summary = panel.getByRole('region', { name: winner.t.summaries, exact: true })
  await summary.getByText(source.sharedSummary, { exact: true }).waitFor()
  require(await summary.getByRole('listitem').count() === 1 && await summary.locator(`time[datetime="${source.selectedOn}"]`).count() === 1
    && await panel.getByText(source.rawBody, { exact: true }).count() === 0
    && await panel.getByText(source.historyProjectTitle, { exact: true }).count() === 0
    && await summary.locator('img, a, textarea, input').count() === 0, 'ACCEPTED_SHARE_REVEALED_ORIGINAL_HISTORY')
  await mobileFits(summary)
}

async function verifyLostAccess(winner, id, source) {
  const { page } = winner
  const panel = detailPanel(winner)
  const denied = panel.getByRole('alert').filter({ hasText: winner.t.unavailable })
  const refresh = button(panel, winner.t.refresh)
  const denials = []
  const recordDenial = response => {
    if (endpoint(response, detailPath(id)) && response.status() === 404) denials.push(response)
  }
  page.on('response', recordDenial)
  try {
    await foreground(page)
    // Focusing another Chromium context may not dispatch a new read. Use the
    // existing Refresh control when automatic revalidation has not run yet.
    const ready = await Promise.race([
      denied.waitFor().then(() => 'denied'),
      refresh.waitFor().then(() => 'refresh'),
    ])
    if (ready === 'refresh' && !await denied.isVisible()) {
      // Automatic revalidation can remove Refresh while the click is resolving.
      await Promise.race([refresh.click(), denied.waitFor()])
    }
    await denied.waitFor()
    require(denials.length > 0 && denials.every(privateHeaders), 'LOST_ACCESS_RESPONSE_REQUIRED_AND_PRIVATE')
    await panel.getByText(source.sharedSummary, { exact: true }).waitFor({ state: 'detached' })
  } finally { page.off('response', recordDenial) }
  await button(panel, winner.t.back).click()
  await listPanel(winner).getByText(winner.t.empty, { exact: true }).waitFor()
  require(await heading(listPanel(winner), source.shareLabel, 3).count() === 0, 'CLOSED_SHARE_REMAINED_IN_RECEIVED_UI')
}

async function openTeam(owner, f) {
  await openProject(owner, f.listing)
  await button(owner.page, owner.t.team).click()
  return owner.page.locator('section').filter({ has: heading(owner.page, owner.t.team, 2) })
}

async function revokeMember(owner, f) {
  const team = await openTeam(owner, f)
  const row = team.getByRole('listitem').filter({ hasText: `#${f.winnerUserCode} ·` })
  const written = await mutation(owner, memberRevokePath(f), button(row, owner.t.memberRevoke),
    { expected_version: f.listingAnchorVersion }, 200, false)
  require(Array.isArray(written.value) && written.value.some(member => member.user_code === f.winnerUserCode
    && member.role === 'professional' && member.active === false), 'EXACT_ASSIGNMENT_REVOKE_REQUIRED')
}

async function grantMember(owner, f) {
  const team = await openTeam(owner, f)
  const form = team.locator('form')
  await form.getByLabel(owner.t.memberCode, { exact: true }).fill(String(f.winnerUserCode))
  const select = form.locator('select')
  require(await select.count() === 1 && await select.evaluate((field, name) => [...field.labels].some(label => [...label.childNodes]
    .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() === name), owner.t.memberRole),
  'PROJECT_ROLE_FIELD_OR_LABEL_CHANGED')
  await select.selectOption('professional')
  const written = await mutation(owner, membersPath(f), button(form, owner.t.memberGrant),
    { expected_version: f.listingAnchorVersion + 1, role: 'professional', user_code: f.winnerUserCode }, 200, false)
  require(Array.isArray(written.value) && written.value.some(member => member.user_code === f.winnerUserCode
    && member.role === 'professional' && member.active === true), 'EXACT_ASSIGNMENT_REGRANT_REQUIRED')
}

async function step(name, test) {
  stage = name
  await test()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), f = fixture.historyShare
  const browser = await chromium.launch({ headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, winner, privatePicker, privateShare, listingShare, privateAccept, privateRevoke, listingAccept
  try {
    await step('current_owner_picker_scoped_and_project_roles', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest, listPath('received'), 401)
      await close(guest)
      for (const role of ['loser', 'outsider', 'admin']) {
        const state = await session(browser, fixture, role)
        let probes = 0
        state.page.on('request', request => { if (new URL(request.url()).pathname.startsWith(`${base}/options/`)) probes += 1 })
        await openProjectHistory(state, f.private)
        require(await button(state.page, state.t.create).count() === 0 && probes === 0, 'NONOWNER_UI_EXPOSED_SHARE_PICKER')
        await read(state, optionsUrl(f.private), 404)
        await read(state, optionsUrl(f.listing), 404)
        if (role === 'admin') {
          require(await button(state.page, state.t.historyNav).count() === 0, 'ADMIN_UI_EXPOSED_PRIVATE_SHARING_INBOX')
          await read(state, listPath('received'), 404)
        } else {
          await openInbox(state)
          await listPanel(state).getByText(state.t.empty, { exact: true }).waitFor()
        }
        await close(state)
      }
      owner = await session(browser, fixture, 'owner', 'es')
      winner = await session(browser, fixture, 'winner')
      await assertPicker(owner, f.private, f)
      await assertPicker(owner, f.listing, f)
      await read(owner, optionsUrl(f.private, f.listing.anchorProjectId), 404)
      await read(owner, optionsUrl(f.private, f.private.historyProjectId), 404)
      await read(winner, optionsUrl(f.private), 404)
      await expectProjectDenied(winner, f.private)
      privatePicker = await openPicker(owner, f.private)
      require(result.stage_writes === 0 && result.action_writes === 0, 'OPENING_HISTORY_MUST_NOT_WRITE')
    })

    await step('private_share_pending_redacted_until_acceptance', async () => {
      privateShare = await createShare(owner, f.private, f, privatePicker)
      await assertListed(owner, privateShare.id, f.private, f, 'sent', 'pending')
      assertShare(await read(owner, detailPath(privateShare.id)), privateShare.id, f.private, f, 'sent', 'pending', true)
      await openInbox(winner)
      const panel = await openShare(winner, f.private)
      await panel.getByText(new RegExp(`^${winner.t.pending} ·`)).waitFor()
      require(await panel.getByRole('region', { name: winner.t.summaries, exact: true }).count() === 0
        && await panel.getByText(f.private.sharedSummary, { exact: true }).count() === 0, 'PENDING_INVITATION_LEAKED_HISTORY')
      await assertListed(winner, privateShare.id, f.private, f, 'received', 'pending')
      assertShare(await read(winner, detailPath(privateShare.id)), privateShare.id, f.private, f, 'received', 'pending', false)
      await read(winner, receiptPath(privateShare.key), 404)
      const nonrecipient = await session(browser, fixture, 'loser')
      await read(nonrecipient, detailPath(privateShare.id), 404)
      await close(nonrecipient)
    })

    await step('accepted_private_share_only_selected_summary', async () => {
      privateAccept = await action(winner, privateShare.id, winner.t.accept, 'accept', 1, 'accepted')
      await verifyVisibleSummary(winner, f.private)
      assertShare(await read(winner, detailPath(privateShare.id)), privateShare.id, f.private, f, 'received', 'accepted', true)
      await assertListed(winner, privateShare.id, f.private, f, 'received', 'accepted')
      await expectProjectDenied(winner, f.private)
      await read(owner, receiptPath(privateAccept), 404)
    })

    await step('explicit_revocation_clears_recipient_and_retains_minimal_receipts', async () => {
      await openShare(owner, f.private)
      privateRevoke = await action(owner, privateShare.id, owner.t.revoke, 'revoke', 2, 'revoked')
      await detailPanel(owner).getByText(new RegExp(`^${owner.t.revoked} ·`)).waitFor()
      assertShare(await read(owner, detailPath(privateShare.id)), privateShare.id, f.private, f, 'sent', 'revoked', true)
      await verifyLostAccess(winner, privateShare.id, f.private)
      await read(winner, detailPath(privateShare.id), 404)
      await assertAbsent(winner, privateShare.id, 'received')
      assertReceipt(await read(winner, receiptPath(privateAccept)), privateShare.id, 'accept', 'accepted', 2)
      assertReceipt(await read(owner, receiptPath(privateRevoke)), privateShare.id, 'revoke', 'revoked', 3)
      await read(winner, receiptPath(privateRevoke), 404)
    })

    await step('listing_share_same_source_acceptance', async () => {
      listingShare = await createShare(owner, f.listing, f)
      await openInbox(winner)
      await openShare(winner, f.listing)
      assertShare(await read(winner, detailPath(listingShare.id)), listingShare.id, f.listing, f, 'received', 'pending', false)
      listingAccept = await action(winner, listingShare.id, winner.t.accept, 'accept', 1, 'accepted')
      await verifyVisibleSummary(winner, f.listing)
      assertShare(await read(winner, detailPath(listingShare.id)), listingShare.id, f.listing, f, 'received', 'accepted', true)
      await expectProjectDenied(winner, f.listing)
    })

    await step('assignment_regrant_does_not_restore_old_share', async () => {
      await revokeMember(owner, f)
      await verifyLostAccess(winner, listingShare.id, f.listing)
      await read(winner, projectPath(f.listing.anchorProjectId), 404)
      await read(owner, detailPath(listingShare.id), 404)
      await grantMember(owner, f)
      const renewed = await read(winner, projectPath(f.listing.anchorProjectId))
      require(renewed.my_role === 'professional', 'ACTIVE_ASSIGNMENT_DID_NOT_RETURN')
      await read(winner, detailPath(listingShare.id), 404)
      await read(owner, detailPath(listingShare.id), 404)
      await assertAbsent(winner, listingShare.id, 'received')
      await assertAbsent(owner, listingShare.id, 'sent')
      assertReceipt(await read(winner, receiptPath(listingAccept)), listingShare.id, 'accept', 'accepted', 2)
      await expectProjectDenied(winner, f.listing)
      await openInbox(winner)
      await listPanel(winner).getByText(winner.t.empty, { exact: true }).waitFor()
      await mobileFits(listPanel(winner))
    })

    await step('only_exact_loopback_history_and_membership_writes', async () => {
      require(result.stage_writes === 2 && result.action_writes === 3 && result.membership_writes === 2 && shareIds.size === 2
        && result.unexpected_browser_mutations === 0 && result.blocked_external_requests <= 100 && !externalNavigation,
      'EXACT_LOOPBACK_HISTORY_WRITES_REQUIRED')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled([...contexts].map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try { await main() }
catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_FAILURE'
  const frames = [...String(error?.stack ?? '').matchAll(/run-history-share-browser\.mjs:(\d+):\d+/g)]
  const source = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  result.source_line = source ? Number(source[1]) : null
  process.exitCode = 1
}
console.log('HISTORY_SHARE_BROWSER_RESULT ' + JSON.stringify(result))
