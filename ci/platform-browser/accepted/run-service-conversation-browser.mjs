import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const ownerBody = 'Synthetic owner message about the pool service.'
const providerBody = 'Mensaje sintético del profesional sobre el servicio de piscina.'
const copy = {
  en: { nav: 'Private service workflow', received: 'Requests for me', request: 'Open request', title: 'Private conversation',
    message: 'Message', send: 'Send message', refresh: 'Refresh', empty: 'No messages yet.',
    denied: 'This record is unavailable or your current account no longer has access.',
    owner: 'Property owner', provider: 'Awarded provider' },
  es: { nav: 'Flujo privado de servicios', received: 'Solicitudes para mí', request: 'Abrir solicitud', title: 'Conversación privada',
    message: 'Mensaje', send: 'Enviar mensaje', refresh: 'Actualizar', empty: 'Todavía no hay mensajes.',
    denied: 'Este registro no está disponible o tu cuenta ya no tiene acceso.',
    owner: 'Persona propietaria', provider: 'Profesional adjudicado' },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_service_conversation', checks: [],
  message_posts: 0, membership_revokes: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
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
  require(hasKeys(value, ['accounts', 'serviceVisit']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const award = value.serviceVisit
  require([award.requestId, award.projectId, award.listingId].every(id => typeof id === 'string' && uuid.test(id))
    && Number.isSafeInteger(award.winnerUserCode) && award.winnerUserCode > 0
    && award.title === 'Browser rehearsal awarded pool service', 'EXACT_SYNTHETIC_AWARD_REQUIRED')
  return value
}

const requestPath = award => `/property-network/service-requests/${award.requestId}`
const conversationPath = award => `${requestPath(award)}/conversation`
const revokePath = award => `/property-network/projects/${award.projectId}/members/${award.winnerUserCode}/revoke`
const historyPath = award => `/property-network/properties/${award.listingId}/history`
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
  const award = fixture.serviceVisit
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
      if (url.origin === api && request.method() === 'POST' && url.pathname === conversationPath(award)
          && ['owner', 'winner'].includes(role)) result.message_posts += 1
      else if (url.origin === api && request.method() === 'POST' && url.pathname === revokePath(award) && role === 'owner') result.membership_revokes += 1
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
  const panel = page.locator('main section').filter({ has: page.getByRole('heading', { name: text.title, exact: true }) })
  return { context, page, text, panel, role }
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

async function openRequest(state, fixture, received) {
  await openRequests(state, fixture, received)
  const { page, text } = state, award = fixture.serviceVisit
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

function assertMessage(row, body, sender) {
  require(hasKeys(row, ['id', 'sender', 'body', 'created_at']) && uuid.test(row.id)
    && row.body === body && row.sender === sender && Number.isFinite(Date.parse(row.created_at)), 'PRIVATE_MESSAGE_SHAPE_OR_SENDER')
}

async function post(state, award, body, sender) {
  const { page, panel, text } = state
  await foreground(page)
  const input = panel.getByLabel(text.message, { exact: true })
  const send = panel.getByRole('button', { name: text.send, exact: true })
  await input.fill(body)
  const [response] = await Promise.all([
    page.waitForResponse(response => endpoint(response, conversationPath(award), 'POST')),
    send.click(),
  ])
  const request = response.request().postDataJSON()
  require(response.status() === 201 && privateHeaders(response) && hasKeys(request, ['submission_id', 'body'])
    && uuid.test(request.submission_id) && request.body === body, 'ACTUAL_PRIVATE_POST_REQUIRED')
  const message = await response.json()
  assertMessage(message, body, sender)
  await panel.getByText(body, { exact: true }).waitFor()
  await page.waitForFunction(({ title }) => [...document.querySelectorAll('main section')].some(section =>
    section.querySelector('h2')?.textContent === title && section.querySelector('textarea')?.value === ''), { title: text.title })
  return { message, submission: request.submission_id }
}

async function showMessage(state, body, sender) {
  const row = state.panel.locator('ol li').filter({ hasText: body })
  await row.getByText(body, { exact: true }).waitFor()
  await row.getByText(state.text[sender], { exact: true }).waitFor()
  require(await row.locator('time[datetime]').count() === 1, 'RENDERED_MESSAGE_TIME_REQUIRED')
}

async function fits(panel) {
  await panel.scrollIntoViewIfNeeded()
  require(await panel.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return innerWidth === 390 && rect.left >= -1 && rect.right <= 391 && document.documentElement.scrollWidth <= 391
  }), 'MOBILE_CONVERSATION_OVERFLOW')
}

async function verifyOpenConversationClosed(state) {
  const { page, panel, text } = state
  await foreground(page)
  const closed = page.waitForFunction(({ title, denied }) => {
    const section = [...document.querySelectorAll('main section')].find(item => item.querySelector('h2')?.textContent === title)
    return !section || [...section.querySelectorAll('[role="alert"]')].some(item => item.textContent === denied)
  }, { title: text.title, denied: text.denied })
  const refresh = panel.getByRole('button', { name: text.refresh, exact: true })
  if (await refresh.isVisible()) await Promise.race([closed, refresh.click()])
  await closed
  await page.getByText(ownerBody, { exact: true }).waitFor({ state: 'detached' })
  await page.getByText(providerBody, { exact: true }).waitFor({ state: 'detached' })
  require(await panel.getByRole('button', { name: text.send, exact: true }).count() === 0,
    'REVOKED_UI_STILL_ALLOWS_SEND')
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), award = fixture.serviceVisit
  const route = conversationPath(award)
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, winner, ownerPost, providerPost
  try {
    await step('exact_role_privacy', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, route, 401)
      await guest.context.close()
      for (const role of ['admin', 'loser', 'outsider']) {
        const actor = await session(browser, fixture, role)
        let requests = 0
        actor.page.on('request', request => { if (new URL(request.url()).pathname === route) requests += 1 })
        if (role === 'outsider') {
          await openRequests(actor, fixture, true)
          await actor.page.getByText('No authorized service requests match this view.', { exact: true }).waitFor()
        } else if (role === 'admin') {
          const [project] = await Promise.all([
            actor.page.waitForResponse(response => endpoint(response, `/property-network/projects/${award.projectId}`)),
            openRequest(actor, fixture, false),
          ])
          require(project.status() === 200 && (await project.json()).my_role === 'admin', 'ADMIN_PROJECT_ROLE_REQUIRED')
        } else {
          const lost = await openRequest(actor, fixture, true)
          require(lost.recipient.status === 'closed' && lost.project_id === null && lost.can_open_project === false,
            'LOSING_PROVIDER_PROJECT_SCOPE_CHANGED')
        }
        require(await actor.panel.count() === 0 && requests === 0, 'UNAUTHORIZED_CONVERSATION_UI_OR_REQUEST')
        await read(actor.context, route, 404)
        await actor.context.close()
      }
    })
    await step('mobile_bilingual_message_exchange', async () => {
      owner = await session(browser, fixture, 'owner')
      const managed = await openRequest(owner, fixture, false)
      require(managed.status === 'awarded' && managed.project_id === award.projectId, 'EXACT_OWNER_AWARD_REQUIRED')
      await owner.panel.getByText(owner.text.empty, { exact: true }).waitFor()
      await fits(owner.panel)
      ownerPost = await post(owner, award, ownerBody, 'owner')
      winner = await session(browser, fixture, 'winner', 'es')
      const received = await openRequest(winner, fixture, true)
      require(received.recipient.status === 'awarded' && received.project_id === award.projectId, 'EXACT_AWARDED_PROVIDER_REQUIRED')
      await showMessage(winner, ownerBody, 'owner')
      await fits(winner.panel)
      providerPost = await post(winner, award, providerBody, 'provider')
      await showMessage(winner, providerBody, 'provider')
      await foreground(owner.page)
      await owner.panel.getByRole('button', { name: owner.text.refresh, exact: true }).click()
      await showMessage(owner, providerBody, 'provider')
      const loaded = await read(owner.context, route)
      require(loaded.request_id === award.requestId && loaded.page === 1 && loaded.total === 2 && loaded.items.length === 2,
        'PRIVATE_CONVERSATION_PAGE_CHANGED')
      require(loaded.items[0].id === providerPost.message.id && loaded.items[1].id === ownerPost.message.id,
        'MESSAGE_ORDER_OR_RECEIPT_CHANGED')
    })
    await step('actor_receipts_and_separate_property_history', async () => {
      for (const [author, other, sent] of [[owner, winner, ownerPost], [winner, owner, providerPost]]) {
        const receipt = `${route}/submissions/${sent.submission}`
        const saved = await read(author.context, receipt)
        require(saved.id === sent.message.id && saved.body === sent.message.body, 'OWN_RECEIPT_CHANGED')
        await read(other.context, receipt, 404)
      }
      const history = await read(owner.context, historyPath(award))
      require(Array.isArray(history.items) && history.items.length === 0, 'PRIVATE_MESSAGE_ENTERED_PROPERTY_HISTORY')
    })
    await step('project_revocation_closes_conversation', async () => {
      const membership = await owner.context.newPage()
      await membership.bringToFront()
      await membership.goto(`${dashboard}/property-network?tab=projects&project=${award.projectId}`, { waitUntil: 'domcontentloaded' })
      await foreground(membership)
      await membership.getByRole('navigation', { name: 'Project sections', exact: true })
        .getByRole('button', { name: 'Project team', exact: true }).click()
      const member = membership.locator('main li').filter({ hasText: `#${award.winnerUserCode} ·` })
      const [revoked] = await Promise.all([
        membership.waitForResponse(response => endpoint(response, revokePath(award), 'POST')),
        member.getByRole('button', { name: 'Revoke project access', exact: true }).click(),
      ])
      require(revoked.status() === 200 && privateHeaders(revoked), 'UI_MEMBERSHIP_REVOCATION_FAILED')
      await membership.close()
      await read(winner.context, route, 404)
      await read(owner.context, route, 404)
      await read(owner.context, `${route}/submissions/${ownerPost.submission}`, 404)
      await read(winner.context, `${route}/submissions/${providerPost.submission}`, 404)
      await verifyOpenConversationClosed(winner)
      await verifyOpenConversationClosed(owner)
    })
    await step('loopback_only_and_no_automatic_writes', async () => {
      require(!externalNavigation && result.message_posts === 2 && result.membership_revokes === 1
        && result.unexpected_browser_mutations === 0, 'UNEXPECTED_BROWSER_NETWORK_EFFECT')
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
  const frame = /run-service-conversation-browser\.mjs:(\d{1,5}):\d+/.exec(String(error?.stack))
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_CONVERSATION_BROWSER_RESULT ${JSON.stringify(result)}\n`)
