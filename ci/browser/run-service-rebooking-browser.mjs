import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const fresh = {
  title: 'Nueva solicitud de piscina de prueba',
  alias: 'Propietaria de prueba',
  brief: 'Solicito una nueva limpieza de la piscina la próxima semana.',
}
const copy = {
  en: { nav: 'Private service workflow', received: 'Requests for me', request: 'Open request',
    open: 'Request again', title: 'Repeat service request', refresh: 'Refresh',
    requestTitle: 'Shared request title', alias: 'Name or alias to share for this request', brief: 'Shared work brief',
    area: 'Shared coarse area (optional)', date: 'Desired start date (optional)', preview: 'Review exact shared request',
    consent: 'I authorize sharing these fields only with the selected members.', send: 'Share this private request',
    completed: 'Work completed and approved', unfinished: 'Visit unscheduled',
    unavailable: 'This service is no longer available to request again.',
  },
  es: { nav: 'Flujo privado de servicios', received: 'Solicitudes para mí', request: 'Abrir solicitud',
    open: 'Solicitar de nuevo', title: 'Repetir solicitud de servicio', refresh: 'Actualizar',
    requestTitle: 'Título compartido de la solicitud', alias: 'Nombre o alias para compartir en esta solicitud',
    brief: 'Descripción compartida del trabajo', area: 'Zona general compartida (opcional)',
    date: 'Fecha deseada de inicio (opcional)', preview: 'Revisar la solicitud exacta que se compartirá',
    consent: 'Autorizo compartir estos campos solo con los miembros seleccionados.', send: 'Compartir esta solicitud privada',
    completed: 'Trabajo finalizado y aprobado', unfinished: 'Visita sin programar',
    unavailable: 'Este servicio ya no está disponible para volver a solicitarlo.',
  },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_service_rebooking', checks: [],
  request_posts: 0, membership_revokes: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = []
let stage = 'fixture'
let externalNavigation = false

class RehearsalFailure extends Error {}
function require(condition, code) {
  if (!condition) throw new RehearsalFailure(code)
}
function hasKeys(value, keys) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(hasKeys(value, ['accounts', 'serviceRebooking']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const group = value.serviceRebooking
  const groupKeys = ['completed', 'unfinished', 'privateCompleted', 'listingTitle', 'providerAlias', 'originalBrief',
    'providerFirstPageAbsent', 'listingFirstPageAbsent']
  require(hasKeys(group, groupKeys) && group.providerFirstPageAbsent === true && group.listingFirstPageAbsent === true
    && group.listingTitle === 'Browser rehearsal villa' && group.providerAlias === 'Synthetic winner service provider'
    && group.originalBrief === 'Synthetic pool service scope', 'EXACT_SYNTHETIC_REBOOKING_FIXTURE_REQUIRED')
  for (const status of ['completed', 'unfinished']) {
    const row = group[status]
    const keys = ['title', 'requestId', 'projectId', 'listingId', 'winnerUserCode']
    const ids = [row.requestId, row.projectId, row.listingId]
    if (status === 'completed') { keys.push('completionUpdateId'); ids.push(row.completionUpdateId) }
    require(hasKeys(row, keys) && ids.every(id => typeof id === 'string' && uuid.test(id))
      && Number.isSafeInteger(row.winnerUserCode) && row.winnerUserCode > 0 && typeof row.title === 'string' && row.title.length > 0,
    'EXACT_SYNTHETIC_AWARD_REQUIRED')
  }
  require(group.completed.requestId !== group.unfinished.requestId && group.completed.projectId !== group.unfinished.projectId
    && group.completed.listingId === group.unfinished.listingId
    && group.completed.winnerUserCode === group.unfinished.winnerUserCode, 'COMMON_FIXTURE_PARTIES_REQUIRED')
  const row = group.privateCompleted
  require(hasKeys(row, ['title', 'requestId', 'projectId', 'propertyId', 'winnerUserCode', 'completionUpdateId'])
    && [row.requestId, row.projectId, row.propertyId, row.completionUpdateId].every(id => typeof id === 'string' && uuid.test(id))
    && row.winnerUserCode === group.completed.winnerUserCode && row.title === 'Browser rehearsal private registry pool service',
  'EXACT_PRIVATE_REGISTRY_AWARD_REQUIRED')
  return value
}

const requestPath = row => `/property-network/service-requests/${row.requestId}`
const rebookPath = row => `${requestPath(row)}/rebook-context`
const revokePath = row => `/property-network/projects/${row.projectId}/members/${row.winnerUserCode}/revoke`
const newRequestPath = '/property-network/service-requests'
const historyPath = row => `/property-network/properties/${row.listingId}/history`

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}
function privateHeaders(response) {
  const headers = response.headers()
  return headers['cache-control'] === 'no-store' && headers.pragma === 'no-cache' && headers['x-content-type-options'] === 'nosniff'
}

async function read(context, path, status = 200) {
  const response = await context.request.get(`${api}${path}`, { timeout: wait, maxRedirects: 0 })
  try {
    require(response.status() === status && privateHeaders(response), 'PRIVATE_READ_STATUS_OR_HEADERS')
    const value = await response.json()
    if (status >= 400) require(hasKeys(value, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return value
  } finally { await response.dispose() }
}

async function session(browser, fixture, role, lang = 'en') {
  const award = fixture.serviceRebooking.completed
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: lang === 'es' ? 'es-DO' : 'en-US', serviceWorkers: 'block' })
  contexts.push(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, lang }) => {
    if (location.origin !== dashboard) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (![api, dashboard].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) externalNavigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      if (url.origin === api && request.method() === 'POST' && role === 'owner' && url.pathname === newRequestPath) result.request_posts += 1
      else if (url.origin === api && request.method() === 'POST' && role === 'owner' && url.pathname === revokePath(award)) result.membership_revokes += 1
      else {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
    }
    return route.continue()
  })
  const account = fixture.accounts[role]
  if (account) {
    const login = await context.request.post(`${api}/auth/login`, { data: account,
      headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
      require(login.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'REAL_SYNTHETIC_AUTH_REQUIRED')
    } finally { await login.dispose() }
  }
  const page = await context.newPage(), text = copy[lang]
  return { context, page, text, role }
}

function composer(state) {
  return state.page.getByRole('region', { name: state.text.title, exact: true })
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

async function openRequests(state, fixture, received) {
  const { page, text, role } = state
  await page.bringToFront()
  const [me] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}/property-network?tab=services`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await me.json()
  require(me.status() === 200 && user.role === roles[role] && user.email === fixture.accounts[role].email, 'SIGNED_IN_ROLE_MISMATCH')
  await foreground(page)
  const nav = page.getByRole('navigation', { name: text.nav, exact: true })
  await nav.waitFor()
  if (received) await nav.getByRole('button', { name: text.received, exact: true }).click()
}

async function openRequest(state, fixture, received, award = fixture.serviceRebooking.completed) {
  await openRequests(state, fixture, received)
  const { page, text } = state
  const card = page.locator('main article').filter({ has: page.getByRole('heading', { name: award.title, exact: true }) })
  const [response] = await Promise.all([
    page.waitForResponse(response => endpoint(response, requestPath(award))
      && new URL(response.url()).searchParams.get('view') === (received ? 'received' : 'managed')),
    card.getByRole('button', { name: text.request, exact: true }).click(),
  ])
  require(response.status() === 200 && privateHeaders(response), 'PRIVATE_REQUEST_UI_UNAVAILABLE')
  await page.getByRole('heading', { name: award.title, exact: true, level: 2 }).waitFor()
  return response.json()
}



function expectContext(value, fixture, row) {
  const group = fixture.serviceRebooking
  const kind = row.propertyId ? 'private' : 'listing'
  const title = row.propertyId ? 'Synthetic private registry source' : group.listingTitle
  require(hasKeys(value, ['request_id', 'source', 'category_id', 'provider']) && value.request_id === row.requestId
    && value.category_id === 'pool_service' && hasKeys(value.source, ['kind', 'id', 'title', 'property_class'])
    && value.source.kind === kind && value.source.id === (row.propertyId ?? row.listingId)
    && value.source.title === title && value.source.property_class === 'house_villa'
    && hasKeys(value.provider, ['user_code', 'member_alias']) && value.provider.user_code === row.winnerUserCode
    && value.provider.member_alias === group.providerAlias, 'EXACT_CURRENT_REBOOK_CONTEXT_REQUIRED')
}

async function fits(panel) {
  await panel.scrollIntoViewIfNeeded()
  require(await panel.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return innerWidth === 390 && rect.left >= -1 && rect.right <= 391 && document.documentElement.scrollWidth <= 391
  }), 'MOBILE_REPEAT_REQUEST_OVERFLOW')
}

async function openDraft(state, fixture, row = fixture.serviceRebooking.completed) {
  const { page, text } = state
  const [response] = await Promise.all([
    page.waitForResponse(r => endpoint(r, rebookPath(row))),
    page.getByRole('button', { name: text.open, exact: true }).click(),
  ])
  require(response.status() === 200 && privateHeaders(response), 'REBOOK_DRAFT_CONTEXT_UNAVAILABLE')
  const context = await response.json()
  expectContext(context, fixture, row)
  const panel = composer(state)
  await panel.getByText(context.source.title, { exact: true }).waitFor()
  await panel.getByText(fixture.serviceRebooking.providerAlias, { exact: false }).waitFor()
  return panel
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

function countListRead(request) {
  const { pathname } = new URL(request.url())
  return ['/property-network/service-providers', '/property-network/properties', '/property-network/registry'].includes(pathname)
}

async function main() {
  const fixture = await readFixture(), group = fixture.serviceRebooking
  const { completed, unfinished } = group
  const path = rebookPath(completed)
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, draft, otherOwner, otherDraft, receiptPath, newId
  let listReads = 0
  try {
    await step('scoped_context_requires_owner_approved_work', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, path, 401)
      await guest.context.close()
      for (const role of ['admin', 'winner', 'loser', 'outsider']) {
        const actor = await session(browser, fixture, role)
        if (['admin', 'winner'].includes(role)) {
          let probes = 0
          actor.page.on('request', request => {
            if (new URL(request.url()).pathname === path) probes += 1
          })
          await openRequest(actor, fixture, role === 'winner')
          await actor.page.getByText(actor.text.completed, { exact: true }).waitFor()
          require(probes === 0 && await actor.page.getByRole('button', { name: actor.text.open, exact: true }).count() === 0,
            'NONOWNER_REBOOK_CONTEXT_PROBED_OR_SHOWN')
        }
        await read(actor.context, path, 404)
        await actor.context.close()
      }
      owner = await session(browser, fixture, 'owner', 'es')
      await openRequest(owner, fixture, false, unfinished)
      await owner.page.getByText(owner.text.unfinished, { exact: true }).waitFor()
      require(await owner.page.getByRole('button', { name: owner.text.open, exact: true }).count() === 0,
        'UNFINISHED_REBOOK_ACTION_SHOWN')
      await read(owner.context, rebookPath(unfinished), 404)
    })
    await step('spanish_mobile_blank_draft_uses_exact_beyond_first_page', async () => {
      const original = await openRequest(owner, fixture, false)
      require(original.status === 'awarded' && original.project_id === completed.projectId && original.brief === group.originalBrief,
        'EXACT_ORIGINAL_AWARD_REQUIRED')
      expectContext(await read(owner.context, path), fixture, completed)
      owner.page.on('request', request => { if (countListRead(request)) listReads += 1 })
      draft = await openDraft(owner, fixture)
      const { text } = owner
      for (const name of [text.requestTitle, text.alias, text.brief, text.area, text.date]) {
        require(await draft.getByLabel(name, { exact: true }).inputValue() === '', 'OLD_CONTENT_COPIED_INTO_NEW_DRAFT')
      }
      require(await draft.getByText(group.originalBrief, { exact: true }).count() === 0
        && !await draft.getByRole('button', { name: text.send, exact: true }).isEnabled()
        && !await draft.getByRole('button', { name: text.preview, exact: true }).isEnabled()
        && result.request_posts === 0 && listReads === 0, 'DRAFT_OPENED_WITHOUT_BLANK_STATE_OR_DIRECT_AUTHORITY')
      await fits(draft)
    })
    await step('english_private_registry_repeat_draft_opens_without_list_reads', async () => {
      const registryOwner = await session(browser, fixture, 'owner')
      await openRequest(registryOwner, fixture, false, group.privateCompleted)
      registryOwner.page.on('request', request => { if (countListRead(request)) listReads += 1 })
      const registryDraft = await openDraft(registryOwner, fixture, group.privateCompleted)
      require(await registryDraft.getByLabel(registryOwner.text.brief, { exact: true }).inputValue() === ''
        && !await registryDraft.getByRole('button', { name: registryOwner.text.send, exact: true }).isEnabled()
        && listReads === 0 && result.request_posts === 0, 'PRIVATE_REPEAT_DRAFT_WAS_NOT_BLANK_OR_DIRECT')
      await fits(registryDraft)
      await registryOwner.context.close()
    })
    await step('explicit_preview_and_one_new_request', async () => {
      const { page, text } = owner
      await draft.getByLabel(text.requestTitle, { exact: true }).fill(fresh.title)
      await draft.getByLabel(text.alias, { exact: true }).fill(fresh.alias)
      await draft.getByLabel(text.brief, { exact: true }).fill(fresh.brief)
      await draft.getByRole('button', { name: text.preview, exact: true }).click()
      const preview = draft.getByRole('region', { name: text.preview, exact: true })
      await preview.getByText(fresh.brief, { exact: true }).waitFor()
      require(await preview.getByText(group.providerAlias, { exact: false }).count() === 1
        && !await draft.getByRole('button', { name: text.send, exact: true }).isEnabled()
        && result.request_posts === 0, 'REQUEST_SENT_WITHOUT_EXPLICIT_ACK')
      await preview.getByLabel(text.consent, { exact: true }).check()
      const [auth, response] = await Promise.all([
        page.waitForResponse(r => endpoint(r, path)),
        page.waitForResponse(r => endpoint(r, newRequestPath, 'POST')),
        draft.getByRole('button', { name: text.send, exact: true }).click(),
      ])
      require(auth.status() === 200 && privateHeaders(auth), 'FRESH_REBOOK_PREFLIGHT_REQUIRED')
      expectContext(await auth.json(), fixture, completed)
      const body = response.request().postDataJSON()
      require(response.status() === 201 && privateHeaders(response)
        && hasKeys(body, ['submission_id','property_id','listing_id','property_class','category_id','title',
          'brief','area_label','requester_alias','desired_start_on','recipient_user_codes','shared_brief_acknowledged'])
        && uuid.test(body.submission_id) && body.property_id === null && body.listing_id === completed.listingId
        && body.category_id === 'pool_service' && body.property_class === 'house_villa'
        && body.title === fresh.title && body.brief === fresh.brief && body.requester_alias === fresh.alias
        && body.area_label === '' && body.desired_start_on === null
        && body.recipient_user_codes.length === 1 && body.recipient_user_codes[0] === completed.winnerUserCode
        && body.shared_brief_acknowledged === true, 'NEW_REQUEST_NOT_SCOPED_TO_FRESH_EXPLICIT_INPUT')
      const created = await response.json()
      newId = created.id
      require(uuid.test(newId) && newId !== completed.requestId && newId !== unfinished.requestId && created.status === 'open'
        && created.project_id === null && created.recipients.length === 1 && created.recipients[0].status === 'pending',
      'NEW_DISTINCT_PENDING_REQUEST_REQUIRED')
      receiptPath = `/property-network/service-actions/submissions/${body.submission_id}`
      await page.getByRole('heading', { name: fresh.title, level: 2, exact: true }).waitFor()
      const winner = await session(browser, fixture, 'winner')
      const inbox = await openRequest(winner, fixture, true, { requestId: newId, title: fresh.title })
      require(inbox.brief === fresh.brief && inbox.recipient.status === 'pending' && inbox.project_id === null,
        'EXACT_RECIPIENT_DID_NOT_RECEIVE_NEW_PRIVATE_REQUEST')
      await read(winner.context, receiptPath, 404)
      await winner.context.close()
    })
    await step('revoked_source_stops_repeat_and_existing_receipt_resolves', async () => {
      otherOwner = await session(browser, fixture, 'owner')
      await openRequest(otherOwner, fixture, false)
      otherDraft = await openDraft(otherOwner, fixture)
      await fits(otherDraft)
      const membership = await otherOwner.context.newPage()
      await membership.goto(`${dashboard}/property-network?tab=projects&project=${completed.projectId}`, { waitUntil: 'domcontentloaded' })
      await foreground(membership)
      const team = membership.getByRole('navigation', { name: 'Project sections', exact: true })
      await team.getByRole('button', { name: 'Project team', exact: true }).click()
      const member = membership.locator('main li').filter({ hasText: `#${completed.winnerUserCode} ·` })
      const [revoked] = await Promise.all([
        membership.waitForResponse(r => endpoint(r, revokePath(completed), 'POST')),
        member.getByRole('button', { name: 'Revoke project access', exact: true }).click(),
      ])
      require(revoked.status() === 200 && privateHeaders(revoked), 'UI_MEMBERSHIP_REVOCATION_FAILED')
      await membership.close()
      await foreground(otherOwner.page)
      const stopped = otherDraft.getByText(otherOwner.text.unavailable, { exact: true })
      const rejected = stopped.waitFor()
      const refresh = otherDraft.getByRole('button', { name: otherOwner.text.refresh, exact: true })
      if (await refresh.isVisible()) await Promise.race([rejected, refresh.click()])
      await rejected
      require(await otherDraft.getByText(group.providerAlias, { exact: false }).count() === 0
        && !await otherDraft.getByRole('button', { name: otherOwner.text.send, exact: true }).isEnabled(),
      'REVOKED_REBOOK_CONTEXT_REMAINED_IN_DRAFT')
      await read(otherOwner.context, path, 404)
      const receipt = await read(owner.context, receiptPath)
      require(receipt.operation === 'request_create' && receipt.target_id === newId
        && receipt.current.id === newId && receipt.current.brief === fresh.brief, 'SEPARATE_NEW_REQUEST_RECEIPT_CHANGED')
      const history = await read(owner.context, historyPath(completed))
      require(history.items.length === 1 && history.items[0].id === completed.completionUpdateId
        && !JSON.stringify(history).includes(fresh.brief), 'REBOOKING_CHANGED_EXISTING_WORK_HISTORY')
    })
    await step('loopback_only_one_new_request_and_no_automatic_writes', async () => {
      require(!externalNavigation && result.request_posts === 1 && result.membership_revokes === 1
        && listReads === 0 && result.unexpected_browser_mutations === 0, 'UNEXPECTED_BROWSER_NETWORK_EFFECT')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try { await main() }
catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message
    : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'UNEXPECTED_BROWSER_FAILURE'
  const frames = [...String(error?.stack).matchAll(/run-service-rebooking-browser\.mjs:(\d{1,5}):\d+/g)]
  const frame = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_REBOOKING_BROWSER_RESULT ${JSON.stringify(result)}\n`)
