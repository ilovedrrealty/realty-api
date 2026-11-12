import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const comment = 'El trabajo se completó a tiempo y el área quedó limpia.'
const copy = {
  en: { nav: 'Private service workflow', received: 'Requests for me', request: 'Open request',
    title: 'Private service review', rating: 'Rating (1–5)', comment: 'Review (optional)',
    save: 'Save review', refresh: 'Refresh', empty: 'No review yet.', score: '5 out of 5',
    completed: 'Work completed and approved', unfinished: 'Visit unscheduled' },
  es: { nav: 'Flujo privado de servicios', received: 'Solicitudes para mí', request: 'Abrir solicitud',
    title: 'Reseña privada del servicio', rating: 'Calificación (1–5)', comment: 'Reseña (opcional)',
    save: 'Guardar reseña', refresh: 'Actualizar', empty: 'Todavía no hay una reseña.', score: '5 de 5',
    completed: 'Trabajo finalizado y aprobado', unfinished: 'Visita sin programar' },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_service_review', checks: [],
  review_posts: 0, membership_revokes: 0, unexpected_browser_mutations: 0, blocked_external_requests: 0 }
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
  require(hasKeys(value, ['accounts', 'serviceReview']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const group = value.serviceReview
  require(hasKeys(group, ['completed', 'unfinished']), 'TWO_SERVICE_AWARDS_REQUIRED')
  for (const [status, row] of Object.entries(group)) {
    const keys = ['title', 'requestId', 'projectId', 'listingId', 'winnerUserCode']
    const ids = [row.requestId, row.projectId, row.listingId]
    if (status === 'completed') { keys.push('completionUpdateId'); ids.push(row.completionUpdateId) }
    const title = status === 'completed' ? 'Browser rehearsal awarded pool service' : 'Browser rehearsal unfinished pool service'
    require(hasKeys(row, keys) && ids.every(id => typeof id === 'string' && uuid.test(id))
      && typeof row.winnerUserCode === 'string' && row.winnerUserCode.length > 0 && row.title === title,
    'EXACT_SYNTHETIC_AWARD_REQUIRED')
  }
  require(group.completed.requestId !== group.unfinished.requestId && group.completed.projectId !== group.unfinished.projectId,
    'COMPLETION_AND_UNFINISHED_FIXTURES_COLLIDE')
  return value
}

const requestPath = row => `/property-network/service-requests/${row.requestId}`
const reviewPath = row => `${requestPath(row)}/review`
const revokePath = row => `/property-network/projects/${row.projectId}/members/${row.winnerUserCode}/revoke`
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
  const award = fixture.serviceReview.completed
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
      if (url.origin === api && request.method() === 'POST' && role === 'owner' && url.pathname === reviewPath(award)) result.review_posts += 1
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

function reviewPanel(state) {
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

async function openRequest(state, fixture, received, award = fixture.serviceReview.completed) {
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


function expectReview(row) {
  require(hasKeys(row, ['id', 'stars', 'comment', 'created_at']) && uuid.test(row.id)
    && row.stars === 5 && row.comment === comment && Number.isFinite(Date.parse(row.created_at)),
  'PRIVATE_REVIEW_SHAPE_OR_VALUE')
}

function expectEnvelope(value, row, canSubmit, savedReview = null) {
  require(hasKeys(value, ['request_id', 'review', 'can_submit']) && value.request_id === row.requestId
    && value.can_submit === canSubmit, 'REVIEW_CAPABILITY_OR_SHAPE')
  if (savedReview === null) require(value.review === null, 'UNEXPECTED_PRIOR_REVIEW')
  else {
    expectReview(value.review)
    require(value.review.id === savedReview.id, 'PRIVATE_REVIEW_ID_CHANGED')
  }
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function checkMobile(state) {
  const panel = reviewPanel(state)
  await panel.scrollIntoViewIfNeeded()
  require(await panel.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return innerWidth === 390 && rect.left >= -1 && rect.right <= 391 && document.documentElement.scrollWidth <= 391
  }), 'MOBILE_REVIEW_OVERFLOW')
}

async function showReview(state) {
  const panel = reviewPanel(state)
  await panel.getByText(state.text.score, { exact: true }).waitFor()
  await panel.getByText(comment, { exact: true }).waitFor()
  require(await panel.locator('time[datetime]').count() === 1, 'REVIEW_TIME_REQUIRED')
  require(await panel.getByRole('button', { name: state.text.save, exact: true }).count() === 0,
    'REVIEW_COMPOSER_STILL_VISIBLE')
  await checkMobile(state)
}

async function requireReviewCleared(state) {
  await foreground(state.page)
  const panel = reviewPanel(state)
  const absent = panel.waitFor({ state: 'detached' })
  const refresh = panel.getByRole('button', { name: state.text.refresh, exact: true })
  if (await refresh.isVisible()) await Promise.race([absent, refresh.click()])
  await absent
  require(await state.page.getByText(comment, { exact: true }).count() === 0
    && await state.page.getByRole('button', { name: state.text.save, exact: true }).count() === 0,
  'REVOKED_REVIEW_REMAINED_IN_DOM')
}

async function main() {
  const fixture = await readFixture()
  const { completed, unfinished } = fixture.serviceReview
  const path = reviewPath(completed)
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, winner, savedReview, receiptPath
  try {
    await step('requires_verified_completion_and_exact_parties', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, path, 401)
      const admin = await session(browser, fixture, 'admin')
      let adminReviewRequests = 0
      admin.page.on('request', request => {
        if (new URL(request.url()).pathname === path) adminReviewRequests += 1
      })
      await openRequest(admin, fixture, false)
      await admin.page.getByText(admin.text.completed, { exact: true }).waitFor()
      require(await reviewPanel(admin).count() === 0 && adminReviewRequests === 0, 'ADMIN_REVIEW_PROBED_OR_SHOWN')
      await read(admin.context, path, 404)
      for (const name of ['outsider', 'loser']) {
        const actor = await session(browser, fixture, name)
        await read(actor.context, path, 404)
        await actor.context.close()
      }
      owner = await session(browser, fixture, 'owner', 'es')
      const incomplete = await openRequest(owner, fixture, false, unfinished)
      require(incomplete.project_id === unfinished.projectId, 'UNFINISHED_REQUEST_AWARD_REQUIRED')
      await owner.page.getByText(owner.text.unfinished, { exact: true }).waitFor()
      require(await reviewPanel(owner).count() === 0, 'UNFINISHED_REVIEW_UI_SHOWN')
      await read(owner.context, reviewPath(unfinished), 404)
      const refused = await owner.context.request.post(`${api}${reviewPath(unfinished)}`, {
        headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait,
        data: { submission_id: '910176a7-8137-47fd-9dbb-88b0d4311107', stars: 5, comment: null },
      })
      try {
        require(refused.status() === 404 && privateHeaders(refused) && hasKeys(await refused.json(), ['detail']),
          'UNFINISHED_REVIEW_MUTATION_WAS_NOT_DENIED')
      } finally { await refused.dispose() }
      await guest.context.close()
      await admin.context.close()
    })
    await step('owner_saves_one_review_on_mobile_in_spanish', async () => {
      winner = await session(browser, fixture, 'winner', 'en')
      const received = await openRequest(winner, fixture, true)
      require(received.project_id === completed.projectId && received.recipient.status === 'awarded', 'EXACT_WINNER_AWARD_REQUIRED')
      await reviewPanel(winner).getByText(winner.text.empty, { exact: true }).waitFor()
      expectEnvelope(await read(winner.context, path), completed, false)
      require(await reviewPanel(winner).getByLabel(winner.text.rating, { exact: true }).count() === 0,
        'PROVIDER_CAN_COMPOSE_REVIEW')
      await checkMobile(winner)
      const managed = await openRequest(owner, fixture, false)
      require(managed.project_id === completed.projectId, 'COMPLETED_REQUEST_AWARD_REQUIRED')
      expectEnvelope(await read(owner.context, path), completed, true)
      const panel = reviewPanel(owner)
      await panel.getByLabel(owner.text.rating, { exact: true }).selectOption('5')
      await panel.getByLabel(owner.text.comment, { exact: true }).fill(comment)
      await checkMobile(owner)
      const [saved] = await Promise.all([
        owner.page.waitForResponse(response => endpoint(response, path, 'POST')),
        panel.getByRole('button', { name: owner.text.save, exact: true }).click(),
      ])
      const request = saved.request().postDataJSON()
      require(saved.status() === 201 && privateHeaders(saved) && hasKeys(request, ['submission_id', 'stars', 'comment'])
        && uuid.test(request.submission_id) && request.stars === 5 && request.comment === comment,
      'ACTUAL_PRIVATE_REVIEW_POST_REQUIRED')
      savedReview = await saved.json()
      expectReview(savedReview)
      receiptPath = `${path}/submissions/${request.submission_id}`
      await showReview(owner)
    })
    await step('provider_reads_on_mobile_and_history_stays_separate', async () => {
      await foreground(winner.page)
      await reviewPanel(winner).getByRole('button', { name: winner.text.refresh, exact: true }).click()
      await showReview(winner)
      expectEnvelope(await read(owner.context, path), completed, false, savedReview)
      expectEnvelope(await read(winner.context, path), completed, false, savedReview)
      const receipt = await read(owner.context, receiptPath)
      expectReview(receipt)
      require(receipt.id === savedReview.id, 'OWNER_RECEIPT_CHANGED')
      await read(winner.context, receiptPath, 404)
      const history = await read(owner.context, historyPath(completed))
      require(history.items.length === 1 && history.items[0].id === completed.completionUpdateId
        && !JSON.stringify(history).includes(comment) && !JSON.stringify(history).includes(savedReview.id),
      'REVIEW_ENTERED_PRIVATE_PROPERTY_HISTORY')
    })
    await step('revocation_clears_open_views_and_receipt_access', async () => {
      const membership = await owner.context.newPage()
      await membership.bringToFront()
      await membership.goto(`${dashboard}/property-network?tab=projects&project=${completed.projectId}`, { waitUntil: 'domcontentloaded' })
      await foreground(membership)
      const team = membership.getByRole('navigation').filter({ has: membership.getByRole('button',
        { name: /^(Project team|Equipo del proyecto)$/ }) })
      await team.getByRole('button', { name: /^(Project team|Equipo del proyecto)$/ }).click()
      const member = membership.locator('main li').filter({ hasText: `#${completed.winnerUserCode} ·` })
      const [revoked] = await Promise.all([
        membership.waitForResponse(response => endpoint(response, revokePath(completed), 'POST')),
        member.getByRole('button', { name: /^(Revoke project access|Revocar acceso al proyecto)$/ }).click(),
      ])
      require(revoked.status() === 200 && privateHeaders(revoked), 'UI_MEMBERSHIP_REVOCATION_FAILED')
      await membership.close()
      await read(winner.context, path, 404)
      await read(owner.context, path, 404)
      await read(owner.context, receiptPath, 404)
      await requireReviewCleared(winner)
      await requireReviewCleared(owner)
    })
    await step('loopback_only_and_no_automatic_writes', async () => {
      require(!externalNavigation && result.review_posts === 1 && result.membership_revokes === 1
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
  const frame = /run-service-review-browser\.mjs:(\d{1,5}):\d+/.exec(String(error?.stack))
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_REVIEW_BROWSER_RESULT ${JSON.stringify(result)}\n`)
