import { createHash } from 'node:crypto'
import { lstat, readFile } from 'node:fs/promises'
import { isDeepStrictEqual } from 'node:util'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const base = '/property-network/registry-custody'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const cases = ['decline', 'cancel', 'accept']
const statuses = { offer: 'pending', decline: 'declined', cancel: 'cancelled', accept: 'accepted' }
const fieldKeys = ['title', 'location', 'address', 'property_class', 'relationship']
const copy = {
  en: {
    nav: 'Property network sections', transfers: 'Property transfers', detail: 'Property custody transfer',
    start: 'Transfer property custody', open: 'Open transfer', received: 'Received', accept: 'Accept transfer',
    acceptConsent: 'I understand what I’m accepting.', confirmAccept: 'Confirm acceptance', decline: 'Decline transfer',
    confirmDecline: 'Confirm decline', pending: 'Pending acceptance', accepted: 'Accepted', declined: 'Declined', cancelled: 'Cancelled',
    fields: 'Transferred property details', openProperty: 'Open private property', refresh: 'Refresh',
  },
  es: {
    nav: 'Secciones de la red de propiedades', transfers: 'Transferencias de propiedades', detail: 'Transferencia de custodia',
    start: 'Transferir la custodia de la propiedad', blocker: 'Trabajos que debes cerrar antes de transferir',
    code: 'Código de miembro del destinatario', label: 'Nombre de la invitación',
    offerConsent: 'Revisé los datos de la propiedad y entiendo qué se transferirá.', submit: 'Enviar invitación de transferencia',
    viewSent: 'Ver transferencias enviadas', sent: 'Enviadas', open: 'Abrir transferencia', cancel: 'Cancelar transferencia',
    confirmCancel: 'Confirmar cancelación', cancelled: 'Cancelada',
    unavailable: 'Este registro no está disponible o tu cuenta ya no tiene acceso.',
    registryRefresh: 'Actualizar registro y conservar mi borrador',
  },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_private_property_custody', checks: [], offer_writes: 0,
  action_writes: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = new Set(), transferIds = new Set()
let stage = 'fixture', externalNavigation = false

class Failure extends Error {}
function require(condition, code) { if (!condition) throw new Failure(code) }
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const exact = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key))

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true' && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(exact(value, ['accounts', 'custody']) && exact(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(exact(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const f = value.custody
  require(exact(f, ['propertyId', 'propertyVersion', 'fields', 'priorNotes', 'recipientUserCode', 'historyProjectId', 'historyProjectTitle',
    'historyBody', 'historyMediaId', 'historyMediaSha256', 'historyShareId', 'priorRequestId', 'creatorSubmissionId', 'blockedPropertyId', 'blockedPropertyTitle',
    'labels', 'expiresAt']) && f.propertyVersion === 1 && Number.isInteger(f.recipientUserCode) && f.recipientUserCode > 0
    && exact(f.labels, cases) && cases.every(name => f.labels[name] === `Synthetic custody fixture ${name}`)
    && Number.isFinite(Date.parse(f.expiresAt)) && Date.parse(f.expiresAt) > Date.now()
    && exact(f.fields, fieldKeys) && f.fields.title === 'Synthetic private custody villa' && f.fields.property_class === 'house_villa'
    && f.fields.relationship === 'owner' && f.fields.location.startsWith('Synthetic ') && f.fields.address.startsWith('Synthetic ')
    && /^Synthetic /.test(f.priorNotes) && /^Synthetic /.test(f.historyBody) && /^[a-f0-9]{64}$/.test(f.historyMediaSha256),
  'EXACT_SYNTHETIC_CUSTODY_SCOPE_REQUIRED')
  const ids = [f.propertyId, f.historyProjectId, f.historyMediaId, f.historyShareId, f.priorRequestId, f.creatorSubmissionId, f.blockedPropertyId]
  require(ids.every(id => uuid.test(id)) && new Set(ids).size === ids.length, 'SYNTHETIC_CUSTODY_IDENTITIES_REQUIRED')
  return value
}

const registry = id => `/property-network/registry/${id}`
const project = id => `/property-network/projects/${id}`
const media = id => `/property-network/media/${id}`
const options = id => `${base}/options/${id}`
const detail = id => `${base}/${id}`
const actions = id => `${detail(id)}/actions`
const receipt = id => `${base}/submissions/${id}`
const listed = view => `${base}?view=${view}&page=1`
const reminders = id => `/property-network/maintenance-reminders/private/${id}`
const oldShare = id => `/property-network/history-shares/${id}`

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}
function noStore(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache' && headers['x-content-type-options'] === 'nosniff'
}
async function read(state, path, status = 200) {
  const response = await state.context.request.get(`${api}${path}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(response.status() === status && noStore(response), 'PRIVATE_READ_STATUS_OR_HEADERS')
    const body = await response.json()
    if (status >= 400) require(exact(body, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return body
  } finally { await response.dispose() }
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
      const relevant = request.method() === 'POST' && url.origin === api && !url.search
      if (relevant && role === 'owner' && url.pathname === base) result.offer_writes += 1
      else if (relevant && ['owner', 'loser'].includes(role) && [...transferIds].some(id => url.pathname === actions(id))) result.action_writes += 1
      else {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
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
  return { context, page: account ? await context.newPage() : null, role, account, lang, t: copy[lang] }
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
  const identity = await auth.json()
  require(auth.status() === 200 && identity.role === roles[role] && identity.email === account.email, 'SIGNED_IN_ROLE_MISMATCH')
  await foreground(page)
  require(await page.evaluate(lang => localStorage.getItem('ildr_lang') === lang && !document.cookie.includes('ildr_token='), state.lang),
    'ISOLATED_BROWSER_CONTEXT_INVALID')
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const heading = (scope, name, level) => scope.getByRole('heading', { name, exact: true, level })
const panel = (state, name) => state.page.getByRole('region', { name, exact: true })
const listPanel = state => panel(state, state.t.transfers)
const detailPanel = state => panel(state, state.t.detail)

async function mobileFits(locator) {
  await locator.scrollIntoViewIfNeeded()
  require(await locator.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return innerWidth === 390 && innerHeight === 844 && bounds.left >= -1 && bounds.right <= innerWidth + 1
      && document.documentElement.scrollWidth <= innerWidth + 1
  }), 'MOBILE_HORIZONTAL_OVERFLOW')
}
async function openProperty(state, id, title) {
  await navigate(state, `/property-network?tab=properties&property=${id}`)
  await heading(state.page, title, 1).waitFor()
}
async function openOffer(state, id, title) {
  await openProperty(state, id, title)
  await button(state.page, state.t.start).click()
  const region = panel(state, state.t.start)
  await region.waitFor()
  await mobileFits(region)
  return region
}
async function openInbox(state) {
  await navigate(state, '/property-network')
  const nav = state.page.getByRole('navigation', { name: state.t.nav, exact: true })
  await button(nav, state.t.transfers).click()
  await button(listPanel(state), state.t.received).waitFor()
}
async function openTransfer(state, label) {
  await foreground(state.page)
  const row = listPanel(state).getByRole('listitem').filter({ has: heading(state.page, label, 3) })
  await button(row, state.t.open).click()
  const region = detailPanel(state)
  await heading(region, label, 3).waitFor()
  await mobileFits(region)
  return region
}

function assertReceipt(value, id, operation) {
  require(exact(value, ['id', 'operation', 'status', 'version']) && uuid.test(value.id) && (!id || value.id === id)
    && value.operation === operation && value.status === statuses[operation] && value.version === (operation === 'offer' ? 1 : 2),
  'EXACT_ACTOR_CUSTODY_RECEIPT_REQUIRED')
  return value.id
}
function assertTransfer(value, f, label, id, view, status, reveal) {
  const expectedActions = status !== 'pending' ? [] : view === 'sent' ? ['cancel'] : ['accept', 'decline']
  require(exact(value, ['id', 'label', 'view', 'status', 'version', 'expires_at', 'fields', 'property_id', 'actions'])
    && value.id === id && value.label === label && value.view === view && value.status === status
    && value.version === (status === 'pending' ? 1 : 2) && Date.parse(value.expires_at) === Date.parse(f.expiresAt)
    && isDeepStrictEqual(value.actions, expectedActions) && isDeepStrictEqual(value.fields, reveal ? f.fields : null)
    && value.property_id === (reveal ? f.propertyId : null), 'CUSTODY_METADATA_OR_FIELD_PROJECTION_WRONG')
}
async function assertListed(state, f, label, id, view, status) {
  const value = await read(state, listed(view))
  require(exact(value, ['view', 'items', 'total', 'page', 'page_size']) && value.view === view && value.page === 1 && value.page_size === 30
    && Array.isArray(value.items) && value.total === value.items.length, 'EXACT_ACTOR_CUSTODY_LIST_REQUIRED')
  assertTransfer(value.items.find(row => row.id === id), f, label, id, view, status, false)
}
function assertRegistry(value, f, final) {
  require(value.id === f.propertyId && value.version === f.propertyVersion + Number(final) && value.can_manage === true
    && value.notes === (final ? '' : f.priorNotes) && fieldKeys.every(key => value[key] === f.fields[key]),
  'EXACT_STABLE_REGISTRY_FIELDS_REQUIRED')
}

async function mutation(state, path, control, expected, status) {
  const [response] = await Promise.all([state.page.waitForResponse(response => endpoint(response, path, 'POST')), control.click()])
  const body = response.request().postDataJSON()
  const { submission_id: key, ...payload } = body
  require(response.status() === status && noStore(response) && uuid.test(key) && isDeepStrictEqual(payload, expected),
    'EXACT_SYNTHETIC_CUSTODY_WRITE_REQUIRED')
  return { value: await response.json(), key }
}

async function makeOffer(owner, f, name) {
  const label = f.labels[name], region = await openOffer(owner, f.propertyId, f.fields.title)
  for (const key of ['title', 'location', 'address']) await region.getByText(f.fields[key], { exact: true }).waitFor()
  require(await region.getByText(f.priorNotes, { exact: true }).count() === 0, 'PRIVATE_NOTES_INCLUDED_IN_OFFER_PREVIEW')
  await region.getByLabel(owner.t.code, { exact: true }).fill(String(f.recipientUserCode))
  await region.getByLabel(owner.t.label, { exact: true }).fill(label)
  const submit = button(region, owner.t.submit)
  require(await submit.isDisabled(), 'SENDER_ACKNOWLEDGMENT_REQUIRED')
  await region.getByRole('checkbox', { name: owner.t.offerConsent, exact: true }).check()
  const written = await mutation(owner, base, submit, { property_id: f.propertyId, expected_property_version: f.propertyVersion,
    recipient_user_code: f.recipientUserCode, label, transfer_acknowledged: true }, 201)
  const id = assertReceipt(written.value, null, 'offer')
  require(!transferIds.has(id), 'TERMINAL_OFFER_ID_WAS_REUSED')
  transferIds.add(id)
  await button(region, owner.t.viewSent).click()
  await button(listPanel(owner), owner.t.sent).waitFor()
  return { id, key: written.key, label }
}

async function expectPending(recipient, f, row) {
  await openInbox(recipient)
  const region = await openTransfer(recipient, row.label)
  await region.getByText(recipient.t.pending, { exact: true }).waitFor()
  require(await region.locator('time').count() === 1 && Date.parse(await region.locator('time').getAttribute('datetime')) === Date.parse(f.expiresAt)
    && await button(region, recipient.t.openProperty).count() === 0 && await heading(region, recipient.t.fields, 4).count() === 0,
  'PENDING_INVITATION_EXPOSED_PROPERTY_AUTHORITY')
  for (const secret of [f.fields.title, f.fields.location, f.fields.address, f.priorNotes, f.historyBody]) {
    require(await region.getByText(secret, { exact: true }).count() === 0, 'PENDING_INVITATION_EXPOSED_PRIVATE_VALUE')
  }
  await assertListed(recipient, f, row.label, row.id, 'received', 'pending')
  assertTransfer(await read(recipient, detail(row.id)), f, row.label, row.id, 'received', 'pending', false)
  await read(recipient, registry(f.propertyId), 404)
  await read(recipient, receipt(row.key), 404)
}

async function decide(state, row, operation, name, confirmation) {
  await foreground(state.page)
  const region = detailPanel(state)
  await button(region, name).click()
  const confirm = button(region, confirmation)
  const expected = { expected_version: 1, operation }
  if (operation === 'accept') {
    require(await confirm.isDisabled(), 'RECIPIENT_ACKNOWLEDGMENT_REQUIRED')
    await region.getByRole('checkbox', { name: state.t.acceptConsent, exact: true }).check()
    expected.transfer_acknowledged = true
  }
  const response = await mutation(state, actions(row.id), confirm, expected, 200)
  assertReceipt(response.value, row.id, operation)
  return response.key
}

async function assertReceipts(owner, recipient, offered, key, operation) {
  const actor = operation === 'cancel' ? owner : recipient, other = actor === owner ? recipient : owner
  assertReceipt(await read(owner, receipt(offered.key)), offered.id, 'offer')
  assertReceipt(await read(actor, receipt(key)), offered.id, operation)
  await read(other, receipt(key), 404)
}

async function clearFormerProperty(owner, f) {
  const { page } = owner
  await foreground(page)
  const denied = page.getByRole('alert').filter({ hasText: owner.t.unavailable })
  const [denial] = await Promise.all([
    page.waitForResponse(response => endpoint(response, registry(f.propertyId)) && response.status() === 404),
    button(page, owner.t.registryRefresh).click(),
  ])
  require(noStore(denial), 'FORMER_CUSTODIAN_REAL_PRIVATE_404_REQUIRED')
  await denied.waitFor()
  await heading(page, f.fields.title, 1).waitFor({ state: 'detached' })
  require(await button(page, owner.t.start).count() === 0, 'FORMER_REGISTRY_ACTION_REMAINED_VISIBLE')
}

async function verifyUninheritedHistory(owner, recipient, provider, f) {
  await read(owner, registry(f.propertyId), 404)
  await read(owner, options(f.propertyId), 404)
  await read(owner, reminders(f.propertyId), 404)
  await read(owner, `/property-network/registry/submissions/${f.creatorSubmissionId}`, 404)
  await read(recipient, `/property-network/registry/submissions/${f.creatorSubmissionId}`, 404)
  await read(recipient, project(f.historyProjectId), 404)
  await read(recipient, `${project(f.historyProjectId)}/updates?page=1`, 404)
  await read(recipient, media(f.historyMediaId), 404)
  const oldRequestPath = `/property-network/service-requests/${f.priorRequestId}`
  await read(recipient, `${oldRequestPath}?view=managed`, 404)
  await read(recipient, `${oldRequestPath}?view=received`, 404)
  const formerRequest = await read(owner, `${oldRequestPath}?view=managed`)
  require(formerRequest.id === f.priorRequestId && formerRequest.status === 'cancelled'
    && formerRequest.capabilities.can_cancel === false && formerRequest.capabilities.can_award === false,
  'FORMER_REQUEST_HISTORY_MUST_REMAIN_CLOSED')
  const history = await read(recipient, `${registry(f.propertyId)}/history?page=1`)
  require(Array.isArray(history.items) && history.items.length === 0 && history.total === 0, 'SUCCESSOR_INHERITED_LEGACY_HISTORY')
  const preference = await read(recipient, reminders(f.propertyId))
  require(isDeepStrictEqual(preference.preference, { enabled: false, lead_days: 7, version: 0 })
    && Array.isArray(preference.items) && preference.items.length === 0, 'SUCCESSOR_INHERITED_PERSONAL_REMINDERS')
  for (const state of [owner, recipient, provider]) await read(state, oldShare(f.historyShareId), 404)
  const original = await read(owner, project(f.historyProjectId))
  const updates = await read(owner, `${project(f.historyProjectId)}/updates?page=1`)
  require(original.my_role === 'owner' && original.title === f.historyProjectTitle && original.status === 'completed'
    && original.can_manage === false && original.can_assign === false && original.can_update === false
    && updates.items.length === 1 && updates.items[0].body === f.historyBody
    && isDeepStrictEqual(updates.items[0].media_ids, [f.historyMediaId]), 'FORMER_HISTORICAL_ACL_OR_BYTES_LINK_CHANGED')
  const photo = await owner.context.request.get(`${api}${media(f.historyMediaId)}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(photo.status() === 200 && noStore(photo) && photo.headers()['content-type'] === 'image/webp'
      && createHash('sha256').update(await photo.body()).digest('hex') === f.historyMediaSha256, 'LEGACY_PRIVATE_PHOTO_NOT_RETAINED_EXACTLY')
  } finally { await photo.dispose() }
}

async function step(name, test) {
  stage = name
  await test()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), f = fixture.custody
  const browser = await chromium.launch({ headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, incoming, provider, accepted, formerPage
  try {
    await step('current_custodian_scope_and_closed_work', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest, listed('received'), 401)
      await close(guest)
      const admin = await session(browser, fixture, 'admin')
      await openProperty(admin, f.propertyId, f.fields.title)
      require(await button(admin.page, admin.t.start).count() === 0 && await button(admin.page, admin.t.transfers).count() === 0,
        'ADMIN_UI_EXPOSED_PERSONAL_CUSTODY_CONTROLS')
      await read(admin, options(f.propertyId), 404)
      await read(admin, listed('received'), 404)
      await close(admin)
      owner = await session(browser, fixture, 'owner', 'es')
      incoming = await session(browser, fixture, 'loser')
      provider = await session(browser, fixture, 'winner')
      await read(incoming, registry(f.propertyId), 404)
      await read(provider, options(f.propertyId), 404)
      await read(provider, oldShare(f.historyShareId), 404)
      const blocked = await openOffer(owner, f.blockedPropertyId, f.blockedPropertyTitle)
      await heading(blocked, owner.t.blocker, 3).waitFor()
      require(await button(blocked, owner.t.submit).count() === 0, 'OPEN_WORK_ALLOWED_CUSTODY_OFFER_FORM')
      const blockedApi = await read(owner, options(f.blockedPropertyId))
      require(isDeepStrictEqual(blockedApi.blockers, { active_projects: 1, open_requests: 0, unfinished_awards: 0 }), 'OPEN_WORK_BLOCKER_NOT_EXACT')
      const ready = await read(owner, options(f.propertyId))
      require(exact(ready, ['property_id', 'property_version', 'fields', 'blockers', 'pending_transfer_id'])
        && ready.property_id === f.propertyId && ready.property_version === f.propertyVersion && ready.pending_transfer_id === null
        && isDeepStrictEqual(ready.fields, f.fields) && isDeepStrictEqual(ready.blockers, { active_projects: 0, open_requests: 0, unfinished_awards: 0 }),
      'CLOSED_WORK_CURRENT_CUSTODY_REQUIRED')
      require(result.offer_writes === 0 && result.action_writes === 0, 'OPENING_CUSTODY_OPTIONS_MUST_NOT_WRITE')
    })

    await step('pending_offer_private_and_recipient_declines', async () => {
      const offered = await makeOffer(owner, f, 'decline')
      await expectPending(incoming, f, offered)
      const outsider = await session(browser, fixture, 'outsider')
      await read(outsider, detail(offered.id), 404)
      await read(outsider, receipt(offered.key), 404)
      require((await read(outsider, listed('received'))).items.length === 0, 'OUTSIDER_HAS_CUSTODY_INVITATION')
      await close(outsider)
      const key = await decide(incoming, offered, 'decline', incoming.t.decline, incoming.t.confirmDecline)
      await detailPanel(incoming).getByText(incoming.t.declined, { exact: true }).waitFor()
      assertTransfer(await read(incoming, detail(offered.id)), f, offered.label, offered.id, 'received', 'declined', false)
      assertRegistry(await read(owner, registry(f.propertyId)), f, false)
      await read(incoming, registry(f.propertyId), 404)
      await assertReceipts(owner, incoming, offered, key, 'decline')
    })

    await step('sender_cancellation_preserves_custody', async () => {
      const offered = await makeOffer(owner, f, 'cancel')
      await expectPending(incoming, f, offered)
      await openTransfer(owner, offered.label)
      const key = await decide(owner, offered, 'cancel', owner.t.cancel, owner.t.confirmCancel)
      await detailPanel(owner).getByText(owner.t.cancelled, { exact: true }).waitFor()
      assertTransfer(await read(incoming, detail(offered.id)), f, offered.label, offered.id, 'received', 'cancelled', false)
      assertRegistry(await read(owner, registry(f.propertyId)), f, false)
      await read(incoming, registry(f.propertyId), 404)
      await assertReceipts(owner, incoming, offered, key, 'cancel')
    })

    await step('bilingual_acceptance_transfers_exact_registry_fields', async () => {
      accepted = await makeOffer(owner, f, 'accept')
      formerPage = { ...owner, page: await owner.context.newPage() }
      await openProperty(formerPage, f.propertyId, f.fields.title)
      await expectPending(incoming, f, accepted)
      const key = await decide(incoming, accepted, 'accept', incoming.t.accept, incoming.t.confirmAccept)
      const region = detailPanel(incoming)
      await region.getByText(incoming.t.accepted, { exact: true }).waitFor()
      const transferred = region.locator('section').filter({ has: heading(incoming.page, incoming.t.fields, 4) })
      await transferred.waitFor()
      require(await transferred.count() === 1, 'EXACT_TRANSFERRED_FIELDS_SECTION_REQUIRED')
      for (const field of ['title', 'location', 'address']) await transferred.getByText(f.fields[field], { exact: true }).waitFor()
      require(await transferred.locator('dd').count() === 5 && await transferred.getByText(f.priorNotes, { exact: true }).count() === 0
        && await transferred.getByText(f.historyBody, { exact: true }).count() === 0, 'TRANSFER_UI_EXPOSED_OUT_OF_SCOPE_FIELDS')
      await mobileFits(transferred)
      assertTransfer(await read(incoming, detail(accepted.id)), f, accepted.label, accepted.id, 'received', 'accepted', true)
      await assertListed(incoming, f, accepted.label, accepted.id, 'received', 'accepted')
      await assertReceipts(owner, incoming, accepted, key, 'accept')
      await button(region, incoming.t.openProperty).click()
      await heading(incoming.page, f.fields.title, 1).waitFor()
      require(new URL(incoming.page.url()).searchParams.get('property') === f.propertyId, 'SUCCESSOR_LINK_DID_NOT_OPEN_STABLE_PROPERTY')
      assertRegistry(await read(incoming, registry(f.propertyId)), f, true)
    })

    await step('former_scope_revoked_and_history_not_inherited', async () => {
      await clearFormerProperty(formerPage, f)
      await verifyUninheritedHistory(owner, incoming, provider, f)
    })

    await step('only_exact_loopback_custody_writes', async () => {
      require(result.offer_writes === 3 && result.action_writes === 3 && transferIds.size === 3
        && result.unexpected_browser_mutations === 0 && result.blocked_external_requests <= 100 && !externalNavigation,
      'EXACT_LOOPBACK_CUSTODY_WRITES_REQUIRED')
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
  result.error_code = error instanceof Failure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_FAILURE'
  const frames = [...String(error?.stack ?? '').matchAll(/run-private-custody-browser\.mjs:(\d+):\d+/g)]
  const source = error instanceof Failure && frames.length > 1 ? frames[1] : frames[0]
  result.source_line = source ? Number(source[1]) : null
  process.exitCode = 1
}
console.log('PRIVATE_CUSTODY_BROWSER_RESULT ' + JSON.stringify(result))
