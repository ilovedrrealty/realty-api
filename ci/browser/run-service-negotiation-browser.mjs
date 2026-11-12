import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const messages = {
  winner: { owner: 'Synthetic owner scope question for the first provider.',
    provider: 'Respuesta privada del primer profesional sobre la piscina.' },
  loser: { owner: 'Synthetic owner scope question for the second provider.',
    provider: 'Respuesta privada del segundo profesional sobre el horario.' },
}
const aliases = { winner: 'Synthetic winner service provider', loser: 'Synthetic loser service provider' }
const copy = {
  en: { nav: 'Private service workflow', received: 'Requests for me', request: 'Open request', title: 'Private conversation before award',
    message: 'Message', send: 'Send message', refresh: 'Refresh', empty: 'No messages yet.',
    denied: 'This record is unavailable or your current account no longer has access.',
    owner: 'Property owner', provider: 'Service provider' },
  es: { nav: 'Flujo privado de servicios', received: 'Solicitudes para mí', request: 'Abrir solicitud', title: 'Conversación privada antes de la adjudicación',
    message: 'Mensaje', send: 'Enviar mensaje', refresh: 'Actualizar', empty: 'Todavía no hay mensajes.',
    denied: 'Este registro no está disponible o tu cuenta ya no tiene acceso.',
    owner: 'Persona propietaria', provider: 'Profesional de servicios' },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_service_negotiation', checks: [],
  message_posts: 0, awards: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
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
  require(hasKeys(value, ['accounts', 'negotiation']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const row = value.negotiation
  require(hasKeys(row, ['title', 'requestId', 'listingId', 'recipientIds'])
    && hasKeys(row.recipientIds, ['winner', 'loser'])
    && [row.requestId, row.listingId, ...Object.values(row.recipientIds)].every(id => typeof id === 'string' && uuid.test(id))
    && row.recipientIds.winner !== row.recipientIds.loser
    && row.title === 'Browser rehearsal pre-award pool service', 'EXACT_SYNTHETIC_NEGOTIATION_REQUIRED')
  return value
}

const requestPath = row => `/property-network/service-requests/${row.requestId}`
const conversationPath = (row, recipient) => `${requestPath(row)}/recipients/${row.recipientIds[recipient]}/conversation`
const awardPath = row => `${requestPath(row)}/award`
const awardedConversationPath = row => `${requestPath(row)}/conversation`
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
  const award = fixture.negotiation
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
      const threadAllowed = ['winner', 'loser'].some(recipient =>
        url.pathname === conversationPath(award, recipient) && (role === 'owner' || role === recipient))
      if (url.origin === api && request.method() === 'POST' && threadAllowed) result.message_posts += 1
      else if (url.origin === api && request.method() === 'POST' && url.pathname === awardPath(award) && role === 'owner') result.awards += 1
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

function section(page, title) {
  return page.locator('main section').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
}
function providerCard(page, provider) {
  return section(page, 'Selected members').locator('article')
    .filter({ has: page.getByRole('heading', { name: aliases[provider], exact: true }) })
}
function panelFor(state, recipient) {
  const title = state.role === 'owner' ? `Private conversation with ${aliases[recipient]}` : state.text.title
  return { ...state, panel: section(state.page, title), title }
}
async function openOwnerThread(owner, recipient) {
  await foreground(owner.page)
  await providerCard(owner.page, recipient).getByRole('button', { name: 'Open private conversation', exact: true }).click()
  const state = panelFor(owner, recipient)
  await state.panel.getByRole('heading', { name: state.title, exact: true }).waitFor()
  return state
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
  const { page, text } = state, award = fixture.negotiation
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

async function post(state, negotiation, recipient, body, sender) {
  const { page, panel, text } = state
  await foreground(page)
  const input = panel.getByLabel(text.message, { exact: true })
  const send = panel.getByRole('button', { name: text.send, exact: true })
  await input.fill(body)
  const [response] = await Promise.all([
    page.waitForResponse(response => endpoint(response, conversationPath(negotiation, recipient), 'POST')),
    send.click(),
  ])
  const request = response.request().postDataJSON()
  require(response.status() === 201 && privateHeaders(response) && hasKeys(request, ['submission_id', 'body'])
    && uuid.test(request.submission_id) && request.body === body, 'ACTUAL_PRIVATE_POST_REQUIRED')
  const message = await response.json()
  assertMessage(message, body, sender)
  await panel.getByText(body, { exact: true }).waitFor()
  await page.waitForFunction(({ title }) => [...document.querySelectorAll('main section')].some(section =>
    section.querySelector('h2')?.textContent === title && section.querySelector('textarea')?.value === ''), { title: state.title })
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
  }, { title: state.title, denied: text.denied })
  const refresh = panel.getByRole('button', { name: text.refresh, exact: true })
  if (await refresh.isVisible()) await Promise.race([closed, refresh.click()])
  await closed
  for (const group of Object.values(messages)) {
    await page.getByText(group.owner, { exact: true }).waitFor({ state: 'detached' })
    await page.getByText(group.provider, { exact: true }).waitFor({ state: 'detached' })
  }
  await panel.getByLabel(text.message, { exact: true }).waitFor({ state: 'detached' })
  require(await panel.getByRole('button', { name: text.send, exact: true }).count() === 0,
    'REVOKED_UI_STILL_ALLOWS_SEND')
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

function assertThread(value, row, recipient, expected) {
  require(hasKeys(value, ['request_id', 'recipient_id', 'items', 'total', 'page', 'page_size'])
    && value.request_id === row.requestId && value.recipient_id === row.recipientIds[recipient]
    && value.page === 1 && value.page_size === 30 && value.total === expected.length
    && value.items.length === expected.length && expected.every((message, index) => value.items[index].id === message.id),
  'EXACT_PRIVATE_RECIPIENT_PAGE_REQUIRED')
}

async function main() {
  const fixture = await readFixture(), row = fixture.negotiation
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  const threads = {}
  let owner, selectedOwner
  try {
    await step('exact_role_and_recipient_access', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, conversationPath(row, 'winner'), 401)
      await guest.context.close()
      const admin = await session(browser, fixture, 'admin')
      let negotiationRequests = 0
      admin.page.on('request', request => {
        if (/\/recipients\/[^/]+\/conversation(?:\/|$)/.test(new URL(request.url()).pathname)) negotiationRequests += 1
      })
      const managed = await openRequest(admin, fixture, false)
      require(managed.status === 'open' && managed.recipients.length === 2, 'ADMIN_SERVICE_REQUEST_FIXTURE_REQUIRED')
      require(await admin.page.getByRole('button', { name: 'Open private conversation', exact: true }).count() === 0
        && negotiationRequests === 0, 'ADMIN_NEGOTIATION_CONTROLS_OR_REQUEST')
      await read(admin.context, conversationPath(row, 'winner'), 404)
      await read(admin.context, conversationPath(row, 'loser') + '/access', 404)
      await admin.context.close()
      const outsider = await session(browser, fixture, 'outsider')
      await openRequests(outsider, fixture, true)
      await outsider.page.getByText('No authorized service requests match this view.', { exact: true }).waitFor()
      require(await outsider.page.getByRole('heading', { name: outsider.text.title, exact: true }).count() === 0,
        'OUTSIDER_NEGOTIATION_PANEL_EXPOSED')
      await read(outsider.context, conversationPath(row, 'winner'), 404)
      await read(outsider.context, conversationPath(row, 'loser'), 404)
      await outsider.context.close()
    })
    await step('bilingual_mobile_recipient_exchange', async () => {
      owner = await session(browser, fixture, 'owner')
      const managed = await openRequest(owner, fixture, false)
      require(managed.status === 'open' && managed.project_id === null
        && managed.recipients.every(item => item.status === 'accepted'), 'OPEN_OWNER_NEGOTIATION_REQUIRED')
      for (const provider of ['winner', 'loser']) {
        selectedOwner = await openOwnerThread(owner, provider)
        const other = provider === 'winner' ? 'loser' : 'winner'
        require(await owner.page.getByRole('heading', { name: `Private conversation with ${aliases[other]}`, exact: true }).count() === 0,
          'MULTIPLE_OWNER_TRANSCRIPTS_OPEN')
        await selectedOwner.panel.getByText(selectedOwner.text.empty, { exact: true }).waitFor()
        await fits(selectedOwner.panel)
        const ownerPost = await post(selectedOwner, row, provider, messages[provider].owner, 'owner')
        const recipient = panelFor(await session(browser, fixture, provider, 'es'), provider)
        const received = await openRequest(recipient, fixture, true)
        require(received.recipient.id === row.recipientIds[provider] && received.recipient.status === 'accepted',
          'PROVIDER_NEGOTIATION_RECIPIENT_CHANGED')
        await showMessage(recipient, messages[provider].owner, 'owner')
        await fits(recipient.panel)
        require(await recipient.page.getByText(messages[other].owner, { exact: true }).count() === 0, 'OTHER_RECIPIENT_TEXT_VISIBLE')
        const providerPost = await post(recipient, row, provider, messages[provider].provider, 'provider')
        await showMessage(recipient, messages[provider].provider, 'provider')
        await foreground(owner.page)
        await selectedOwner.panel.getByRole('button', { name: selectedOwner.text.refresh, exact: true }).click()
        await showMessage(selectedOwner, messages[provider].provider, 'provider')
        require(await selectedOwner.panel.getByText(messages[other].owner, { exact: true }).count() === 0, 'OWNER_SWITCH_MIXED_TRANSCRIPTS')
        threads[provider] = { recipient, ownerPost, providerPost }
      }
    })
    await step('cross_recipient_and_receipt_isolation', async () => {
      await foreground(owner.page)
      const draft = selectedOwner.panel.getByLabel(selectedOwner.text.message, { exact: true })
      const unsent = 'Synthetic unsent draft stays with the second provider.'
      await draft.fill(unsent)
      const closedDialog = owner.page.waitForEvent('dialog').then(async dialog => {
        const kind = dialog.type()
        await dialog.dismiss()
        require(kind === 'confirm', 'PRIVATE_DRAFT_GUARD_NOT_A_CONFIRMATION')
      })
      await Promise.all([closedDialog,
        providerCard(owner.page, 'winner').getByRole('button', { name: 'Open private conversation', exact: true }).click()])
      require(await draft.inputValue() === unsent && await panelFor(owner, 'winner').panel.count() === 0,
        'UNSENT_DRAFT_CROSSED_RECIPIENTS')
      await draft.fill('')
      for (const provider of ['winner', 'loser']) {
        const other = provider === 'winner' ? 'loser' : 'winner'
        const path = conversationPath(row, provider)
        const { recipient, ownerPost, providerPost } = threads[provider]
        const expected = [providerPost.message, ownerPost.message]
        assertThread(await read(owner.context, path), row, provider, expected)
        assertThread(await read(recipient.context, path), row, provider, expected)
        await read(threads[other].recipient.context, path, 404)
        await read(threads[other].recipient.context, path + '/access', 404)
        const ownerReceipt = `${path}/submissions/${ownerPost.submission}`
        const providerReceipt = `${path}/submissions/${providerPost.submission}`
        require((await read(owner.context, ownerReceipt)).id === ownerPost.message.id
          && (await read(recipient.context, providerReceipt)).id === providerPost.message.id, 'SENDER_RECEIPT_CHANGED')
        await read(recipient.context, ownerReceipt, 404)
        await read(owner.context, providerReceipt, 404)
        await read(owner.context, `${conversationPath(row, other)}/submissions/${ownerPost.submission}`, 404)
      }
    })
    await step('real_award_closes_open_negotiations', async () => {
      const watchingOwner = await session(browser, fixture, 'owner')
      await openRequest(watchingOwner, fixture, false)
      const watched = await openOwnerThread(watchingOwner, 'loser')
      await showMessage(watched, messages.loser.owner, 'owner')
      await showMessage(watched, messages.loser.provider, 'provider')
      await foreground(owner.page)
      const quote = owner.page.locator('main article').filter({ has: owner.page.getByRole('heading', { name: aliases.winner, exact: true }) })
        .filter({ has: owner.page.getByRole('button', { name: 'Review award', exact: true }) })
      await quote.getByRole('button', { name: 'Review award', exact: true }).click()
      const editor = section(owner.page, 'Review award')
      await editor.getByRole('checkbox').check()
      const [awarded] = await Promise.all([
        owner.page.waitForResponse(response => endpoint(response, awardPath(row), 'POST')),
        editor.getByRole('button', { name: 'Award and create private project', exact: true }).click(),
      ])
      const payload = awarded.request().postDataJSON(), status = await awarded.json()
      require(awarded.status() === 200 && privateHeaders(awarded) && payload.recipient_id === row.recipientIds.winner
        && payload.project_scope_acknowledged === true && status.request.status === 'awarded'
        && status.request.project_id === status.project.id && uuid.test(status.project.id), 'REAL_WINNER_AWARD_REQUIRED')
      for (const provider of ['winner', 'loser']) {
        const { recipient, ownerPost, providerPost } = threads[provider]
        const path = conversationPath(row, provider)
        await read(recipient.context, path, 404)
        await read(owner.context, path, 404)
        await read(recipient.context, `${path}/submissions/${providerPost.submission}`, 404)
        await read(owner.context, `${path}/submissions/${ownerPost.submission}`, 404)
        await verifyOpenConversationClosed(recipient)
      }
      await verifyOpenConversationClosed(watched)
    })
    await step('award_history_starts_clean_and_no_automatic_sends', async () => {
      const awardedPath = awardedConversationPath(row)
      for (const state of [owner, threads.winner.recipient]) {
        const awarded = await openRequest(state, fixture, state.role !== 'owner')
        require(awarded.project_id && (state.role === 'owner' ? awarded.status === 'awarded' : awarded.recipient.status === 'awarded'),
          'AWARDED_REQUEST_NOT_VISIBLE_TO_PARTICIPANT')
        const chat = section(state.page, state.role === 'owner' ? 'Private conversation' : 'Conversación privada')
        await chat.getByText(state.text.empty, { exact: true }).waitFor()
        const persisted = await read(state.context, awardedPath)
        require(persisted.request_id === row.requestId && persisted.total === 0 && persisted.items.length === 0, 'NEGOTIATION_COPIED_TO_AWARD')
      }
      await read(threads.loser.recipient.context, awardedPath, 404)
      const history = await read(owner.context, historyPath(row))
      require(Array.isArray(history.items) && history.items.length === 0, 'NEGOTIATION_COPIED_TO_PROPERTY_HISTORY')
      require(!externalNavigation && result.message_posts === 4 && result.awards === 1
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
  const frame = /run-service-negotiation-browser\.mjs:(\d{1,5}):\d+/.exec(String(error?.stack))
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_NEGOTIATION_BROWSER_RESULT ${JSON.stringify(result)}\n`)
