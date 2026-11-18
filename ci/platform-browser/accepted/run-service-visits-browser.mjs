import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const DASHBOARD = 'http://127.0.0.1:4175'
const API = 'http://127.0.0.1:8000'
const FIXTURE_PATH = '/browser-fixtures/fixtures.json'
const CLOCK_PATH = '/__service-visit-rehearsal/advance-clock'
const WAIT = 14_000
const MOBILE = { width: 390, height: 844 }
const ROLES = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const WORK_KEYS = ['request_id', 'project_id', 'version', 'status', 'confirmed_visit', 'proposed_visit',
  'completion_note', 'completed_on', 'completion_review_note', 'completion_update_id', 'actions']
const INITIAL = 'Synthetic initial completion for service visit rehearsal.'
const REVIEW = 'Synthetic private review feedback for the awarded provider only.'
const APPROVED = 'Synthetic approved completion for property service history.'

const en = {
  navigation: 'Private service workflow', received: 'Requests for me', openRequest: 'Open request',
  panel: 'Service visit and completion', refresh: 'Refresh', confirm: 'Confirm action',
  start: 'Start date and time (Santo Domingo)', duration: 'Duration in minutes (15–480)',
  date: 'Date work was completed (Santo Domingo)', summary: 'Completion summary',
  review: 'Private note about requested changes', timezone: 'Times are shown in Santo Domingo (UTC−04:00).',
  openHistory: 'Open project timeline', timeline: 'Progress timeline', projectTabs: 'Project sections',
  historyTab: 'Property service history', history: 'Property service history',
  actions: { propose_visit: 'Propose a visit time', accept_visit: 'Accept proposed visit',
    decline_visit: 'Decline proposed visit', cancel_visit: 'Cancel the visit',
    request_completion: 'Request completion approval', request_changes: 'Request changes', approve_completion: 'Approve completion' },
  states: { unscheduled: 'Visit unscheduled', proposed: 'Proposed visit awaiting confirmation', confirmed: 'Visit confirmed',
    cancelled: 'Visit canceled', completion_requested: 'Completion awaiting approval', completed: 'Work completed and approved' },
}
const es = {
  navigation: 'Flujo privado de servicios', received: 'Solicitudes para mí', openRequest: 'Abrir solicitud',
  panel: 'Visita de servicio y aprobación del trabajo', refresh: 'Actualizar', confirm: 'Confirmar acción',
  start: 'Fecha y hora de inicio (Santo Domingo)', duration: 'Duración en minutos (15–480)',
  date: 'Fecha de finalización del trabajo (Santo Domingo)', summary: 'Resumen del trabajo realizado',
  review: 'Nota privada sobre los cambios solicitados', timezone: 'Las horas se muestran en el horario de Santo Domingo (UTC−04:00).',
  openHistory: 'Abrir el registro de avances del proyecto', timeline: 'Cronología del progreso', projectTabs: 'Secciones del proyecto',
  historyTab: 'Historial de servicios', history: 'Historial de servicios de la propiedad',
  actions: { propose_visit: 'Proponer una fecha y hora', accept_visit: 'Aceptar la visita propuesta',
    decline_visit: 'Rechazar la visita propuesta', cancel_visit: 'Cancelar la visita',
    request_completion: 'Solicitar la aprobación del trabajo', request_changes: 'Solicitar cambios', approve_completion: 'Aprobar el trabajo realizado' },
  states: { unscheduled: 'Visita sin programar', proposed: 'Visita propuesta pendiente de confirmación', confirmed: 'Visita confirmada',
    cancelled: 'Visita cancelada', completion_requested: 'Trabajo pendiente de aprobación', completed: 'Trabajo finalizado y aprobado' },
}

const result = { status: 'FAIL', scope: 'isolated_synthetic_service_visits_browser', checks: [],
  ui_work_posts: 0, blocked_external_requests: 0, unexpected_browser_mutations: 0,
  local_clock_advances: 0, online_override_pages: 0 }
const contexts = new Set()
const submissionIds = new Set()
let stage = 'fixture'
let externalNavigation = false

class TestFailure extends Error {}

function check(condition, reason) {
  if (!condition) throw new TestFailure(reason)
}

function keys(value, expected) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key))
}

async function fixture() {
  const file = process.env.ILDR_BROWSER_FIXTURES
  check(file === FIXTURE_PATH && process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api', 'HOSTED_FIXTURE_REQUIRED')
  check((await lstat(file)).isFile(), 'REGULAR_FIXTURE_REQUIRED')
  const data = await readFile(file, 'utf8')
  check(data.length > 0 && data.length <= 12_000, 'FIXTURE_SIZE_INVALID')
  const seed = JSON.parse(data)
  check(keys(seed, ['accounts', 'serviceVisit']) && keys(seed.accounts, Object.keys(ROLES)), 'FIXTURE_ACCOUNT_SHAPE_INVALID')
  for (const [name, value] of Object.entries(seed.accounts)) {
    check(keys(value, ['email', 'password']) && value.email === `browser-${name}@example.com`
      && typeof value.password === 'string' && value.password.length >= 8 && value.password.length <= 200,
    'SYNTHETIC_ACCOUNT_REQUIRED')
  }
  const work = seed.serviceVisit
  check(keys(work, ['requestId', 'projectId', 'listingId', 'winnerUserCode', 'loserUserCode', 'title', 'projectTitle',
    'clockStart', 'clockAfter']), 'SERVICE_VISIT_FIXTURE_SHAPE_INVALID')
  check([work.requestId, work.projectId, work.listingId].every(value => typeof value === 'string' && UUID.test(value))
    && new Set([work.requestId, work.projectId, work.listingId]).size === 3
    && [work.winnerUserCode, work.loserUserCode].every(value => Number.isSafeInteger(value) && value > 0)
    && work.winnerUserCode !== work.loserUserCode && [work.title, work.projectTitle].every(value =>
      typeof value === 'string' && value.length > 0 && value.length <= 180)
    && Date.parse(work.clockStart) === Date.parse('2026-11-12T13:00:00Z')
    && Date.parse(work.clockAfter) === Date.parse('2026-11-12T18:00:00Z'), 'SERVICE_VISIT_FIXTURE_VALUES_INVALID')
  return seed
}

const requestPath = seed => `/property-network/service-requests/${seed.requestId}`
const workPath = seed => `${requestPath(seed)}/work`
const projectPath = seed => `/property-network/projects/${seed.projectId}`
const historyPath = seed => `/property-network/properties/${seed.listingId}/history`

function matches(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === API && url.pathname === path && response.request().method() === method
}

function privateHeaders(response) {
  const h = response.headers()
  return h['cache-control'] === 'no-store' && h.pragma === 'no-cache' && h['x-content-type-options'] === 'nosniff'
}

async function read(context, path, expected = 200) {
  const response = await context.request.get(`${API}${path}`, { maxRedirects: 0, timeout: WAIT })
  try {
    check(response.status() === expected && privateHeaders(response), 'PRIVATE_READ_STATUS_OR_HEADERS')
    const body = await response.json()
    if (expected >= 400) check(keys(body, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return body
  } finally {
    await response.dispose()
  }
}

async function contextFor(browser, account, name, lang, seed) {
  const context = await browser.newContext({ viewport: MOBILE, locale: lang === 'es' ? 'es-DO' : 'en-US', serviceWorkers: 'block' })
  contexts.add(context)
  context.setDefaultTimeout(WAIT)
  context.setDefaultNavigationTimeout(WAIT)
  await context.addInitScript(({ origin, lang }) => {
    if (location.origin !== origin) return
    localStorage.setItem('ildr_lang', lang)
    const native = navigator.onLine
    if (!native) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
    Object.defineProperty(window, '__ildrServiceVisitNativeOnline', { value: native })
  }, { origin: DASHBOARD, lang })
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (![DASHBOARD, API].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) externalNavigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      if (url.origin === API && url.pathname === workPath(seed) && request.method() === 'POST'
          && ['owner', 'winner'].includes(name)) {
        result.ui_work_posts += 1
      } else {
        result.unexpected_browser_mutations += 1
        return route.abort('blockedbyclient')
      }
    }
    return route.continue()
  })
  if (account) {
    const response = await context.request.post(`${API}/auth/login`, { data: account, headers: { Origin: DASHBOARD },
      maxRedirects: 0, timeout: WAIT })
    try {
      const cookie = (await context.cookies(API)).find(cookie => cookie.name === 'ildr_token')
      check(response.status() === 200 && cookie?.httpOnly && cookie.path === '/' && cookie.domain === '127.0.0.1', 'ACTUAL_LOGIN_COOKIE_REQUIRED')
    } finally {
      await response.dispose()
    }
  }
  return context
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

function section(page, title) {
  return page.locator('main section').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
}

async function mobileFits(locator) {
  await locator.scrollIntoViewIfNeeded()
  check(await locator.evaluate(element => {
    const box = element.getBoundingClientRect()
    return innerWidth === 390 && box.width > 0 && box.left >= -1 && box.right <= innerWidth + 1
      && document.documentElement.scrollWidth <= innerWidth + 1
  }), 'MOBILE_CONTENT_OVERFLOW')
}

async function openServices(browser, seed, name, lang = 'en') {
  const f = seed.serviceVisit
  const context = await contextFor(browser, seed.accounts[name], name, lang, f)
  const page = await context.newPage()
  await page.clock.setFixedTime(new Date(f.clockStart))
  await page.bringToFront()
  const [identity] = await Promise.all([
    page.waitForResponse(response => matches(response, '/auth/me')),
    page.goto(`${DASHBOARD}/property-network?tab=services`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await identity.json()
  check(identity.status() === 200 && user.email === seed.accounts[name].email && user.role === ROLES[name], 'ACTUAL_BROWSER_IDENTITY_REQUIRED')
  await foreground(page)
  const client = await page.evaluate(() => ({ secret: document.cookie.includes('ildr_token='),
    native: window.__ildrServiceVisitNativeOnline, language: localStorage.getItem('ildr_lang') }))
  check(!client.secret && typeof client.native === 'boolean' && client.language === lang, 'ISOLATED_BROWSER_CONTEXT_INVALID')
  result.online_override_pages += Number(!client.native)
  const t = lang === 'es' ? es : en
  const navigation = page.getByRole('navigation', { name: t.navigation, exact: true })
  await navigation.waitFor()
  return { context, page, t, panel: section(page, t.panel), navigation }
}

async function close(context) {
  await context.close()
  contexts.delete(context)
}

async function openRequest(session, seed, received) {
  const { page, t, navigation } = session
  if (received) await navigation.getByRole('button', { name: t.received, exact: true }).click()
  const article = page.locator('main article').filter({ has: page.getByRole('heading', { name: seed.title, exact: true }) })
  const [detail] = await Promise.all([
    page.waitForResponse(response => matches(response, requestPath(seed))
      && new URL(response.url()).searchParams.get('view') === (received ? 'received' : 'managed')),
    article.getByRole('button', { name: t.openRequest, exact: true }).click(),
  ])
  check(detail.status() === 200 && privateHeaders(detail), 'BROWSER_REQUEST_DETAIL_FAILED')
  const body = await detail.json()
  check(body.id === seed.requestId && body.view === (received ? 'received' : 'managed'), 'BROWSER_REQUEST_SCOPE_CHANGED')
  await page.getByRole('heading', { name: seed.title, exact: true }).waitFor()
  return body
}

function assertWork(value, seed, version, status) {
  check(keys(value, WORK_KEYS) && value.request_id === seed.requestId && value.project_id === seed.projectId
    && value.version === version && value.status === status && Array.isArray(value.actions), 'EXACT_WORK_STATE_REQUIRED')
  return value
}

function stateText(session, state) {
  return session.panel.locator('p').filter({ hasText: new RegExp(`^${session.t.states[state]}$`) })
}

async function refreshWork(session, seed, version, status) {
  await foreground(session.page)
  const [response] = await Promise.all([
    session.page.waitForResponse(response => matches(response, workPath(seed))),
    session.panel.getByRole('button', { name: session.t.refresh, exact: true }).click(),
  ])
  check(response.status() === 200 && privateHeaders(response), 'BROWSER_WORK_REFRESH_FAILED')
  const row = assertWork(await response.json(), seed, version, status)
  await stateText(session, status).waitFor()
  return row
}

function checkVisit(visit, wallTime, duration, by = null) {
  check(keys(visit, by ? ['starts_at', 'duration_minutes', 'proposed_by'] : ['starts_at', 'duration_minutes'])
    && Date.parse(visit.starts_at) === Date.parse(`${wallTime}:00-04:00`)
    && visit.duration_minutes === duration && (!by || visit.proposed_by === by), 'SANTO_DOMINGO_VISIT_TIME_CHANGED')
}

async function submit(session, seed, operation, version, status, fields = {}) {
  const { page, panel, t } = session
  await foreground(page)
  await panel.getByRole('button', { name: t.actions[operation], exact: true }).click()
  const form = panel.locator('form')
  const confirm = form.getByRole('button', { name: t.confirm, exact: true })
  await confirm.waitFor()
  if (operation === 'propose_visit') {
    check(await confirm.isDisabled(), 'EMPTY_VISIT_DRAFT_ALLOWED')
    await form.getByLabel(t.start, { exact: true }).fill(fields.starts_at.slice(0, 16))
    await form.getByLabel(t.duration, { exact: true }).fill(String(fields.duration_minutes))
    await mobileFits(form)
  }
  if (operation === 'request_completion') {
    await form.getByLabel(t.date, { exact: true }).fill(fields.completed_on)
    const summary = form.locator('textarea')
    check(await summary.count() === 1 && await summary.evaluate(element =>
      [...(element.closest('label')?.childNodes ?? [])]
        .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim()) === t.summary,
    'COMPLETION_SUMMARY_FIELD_OR_LABEL_CHANGED')
    check(await summary.inputValue() === (version === 11 ? INITIAL : ''), 'EXPECTED_COMPLETION_PREFILL_CHANGED')
    await summary.fill(fields.note)
  }
  if (operation === 'request_changes') await form.getByLabel(t.review, { exact: true }).fill(fields.note)
  if (operation === 'approve_completion') {
    const checkbox = form.getByRole('checkbox')
    check(!await checkbox.isChecked() && await confirm.isDisabled(), 'HISTORY_APPROVAL_NOT_EXPLICIT')
    await checkbox.check()
  }
  const [response] = await Promise.all([
    page.waitForResponse(response => matches(response, workPath(seed), 'POST')),
    confirm.click(),
  ])
  check(response.status() === 200 && privateHeaders(response)
    && await response.request().headerValue('origin') === DASHBOARD, 'ACTUAL_WORK_MUTATION_REQUIRED')
  const payload = response.request().postDataJSON()
  check(keys(payload, ['submission_id', 'expected_version', 'operation', ...Object.keys(fields)])
    && UUID.test(payload.submission_id) && !submissionIds.has(payload.submission_id)
    && payload.operation === operation && payload.expected_version === version
    && Object.entries(fields).every(([key, value]) => payload[key] === value), 'WORK_MUTATION_PAYLOAD_CHANGED')
  submissionIds.add(payload.submission_id)
  const value = assertWork(await response.json(), seed, version + 1, status)
  await form.waitFor({ state: 'detached' })
  await stateText(session, status).waitFor()
  return { value, submissionId: payload.submission_id }
}

async function provePrivacy(browser, fixtureData) {
  const seed = fixtureData.serviceVisit
  const anonymous = await contextFor(browser, null, 'anonymous', 'en', seed)
  await read(anonymous, workPath(seed), 401)
  await close(anonymous)

  const loser = await openServices(browser, fixtureData, 'loser')
  let workReads = 0
  loser.page.on('request', request => { if (request.url() === `${API}${workPath(seed)}`) workReads += 1 })
  const lost = await openRequest(loser, seed, true)
  check(lost.recipient.status === 'closed' && lost.project_id === null && !lost.can_open_project
    && await loser.panel.count() === 0 && workReads === 0, 'LOSER_SAW_PRIVATE_WORK_PANEL')
  await read(loser.context, workPath(seed), 404)
  await read(loser.context, `${projectPath(seed)}/updates`, 404)
  await close(loser.context)

  const outsider = await openServices(browser, fixtureData, 'outsider')
  await outsider.navigation.getByRole('button', { name: outsider.t.received, exact: true }).click()
  await outsider.page.getByText('No authorized service requests match this view.', { exact: true }).waitFor()
  check(await outsider.panel.count() === 0, 'OUTSIDER_SAW_PRIVATE_WORK_PANEL')
  await read(outsider.context, workPath(seed), 404)
  await read(outsider.context, historyPath(seed), 404)
  await close(outsider.context)

  const admin = await openServices(browser, fixtureData, 'admin')
  await openRequest(admin, seed, false)
  await stateText(admin, 'unscheduled').waitFor()
  const readOnly = assertWork(await read(admin.context, workPath(seed)), seed, 0, 'unscheduled')
  check(readOnly.actions.length === 0 && await admin.panel.getByRole('button').count() === 1
    && await admin.panel.locator('form').count() === 0, 'ADMIN_SERVICE_WORK_NOT_READ_ONLY')
  await close(admin.context)
}

async function verifyHistory(session, seed, updateId, completedOn) {
  const { page, panel, t } = session
  await foreground(page)
  const [timelineResponse] = await Promise.all([
    page.waitForResponse(response => matches(response, `${projectPath(seed)}/updates`)),
    panel.getByRole('button', { name: t.openHistory, exact: true }).click(),
  ])
  await page.getByRole('heading', { name: seed.projectTitle, exact: true }).waitFor()
  const location = new URL(page.url())
  check(location.origin === DASHBOARD && location.pathname === '/property-network'
    && location.searchParams.get('tab') === 'projects' && location.searchParams.get('project') === seed.projectId,
  'ACTUAL_PROJECT_LINK_REQUIRED')
  const assertEntry = async response => {
    check(response.status() === 200 && privateHeaders(response), 'PRIVATE_HISTORY_RESPONSE_REQUIRED')
    const payload = await response.json()
    check(Array.isArray(payload.items) && payload.items.length === 1, 'EXPECTED_EXACTLY_ONE_HISTORY_ENTRY')
    const [row] = payload.items
    const serialized = JSON.stringify(payload)
    check(row.id === updateId && row.project_id === seed.projectId && row.kind === 'progress' && row.body === APPROVED
      && row.occurred_on === completedOn && Array.isArray(row.media_ids) && row.media_ids.length === 0
      && !serialized.includes(REVIEW) && !serialized.includes(INITIAL), 'HISTORY_DATA_SCOPE_CHANGED')
  }
  await assertEntry(timelineResponse)
  const timeline = section(page, t.timeline)
  await timeline.getByText(APPROVED, { exact: true }).waitFor()
  check(await timeline.getByText(REVIEW, { exact: true }).count() === 0, 'PRIVATE_REVIEW_VISIBLE_IN_TIMELINE')
  const [propertyResponse] = await Promise.all([
    page.waitForResponse(response => matches(response, historyPath(seed))),
    page.getByRole('navigation', { name: t.projectTabs, exact: true }).getByRole('button', { name: t.historyTab, exact: true }).click(),
  ])
  await assertEntry(propertyResponse)
  const history = section(page, t.history)
  await history.getByText(APPROVED, { exact: true }).waitFor()
  check(await history.getByText(REVIEW, { exact: true }).count() === 0, 'PRIVATE_REVIEW_VISIBLE_IN_PROPERTY_HISTORY')
  await mobileFits(history)
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function main() {
  const fixtureData = await fixture()
  const seed = fixtureData.serviceVisit
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    executablePath: process.env.ILDR_CHROMIUM_PATH || undefined,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  const completedOn = '2026-11-12'
  const at10 = `${completedOn}T10:00`
  const at11 = `${completedOn}T11:00`
  const at12 = `${completedOn}T12:00`
  const visitFields = (time, duration) => ({ starts_at: `${time}:00-04:00`, duration_minutes: duration })
  let owner, winner, approval
  try {
    await step('role_scoped_access', () => provePrivacy(browser, fixtureData))
    await step('mutual_schedule_mobile_en_es', async () => {
      owner = await openServices(browser, fixtureData, 'owner')
      const managed = await openRequest(owner, seed, false)
      check(managed.status === 'awarded' && managed.project_id === seed.projectId && managed.can_open_project, 'OWNER_AWARD_REQUIRED')
      await refreshWork(owner, seed, 0, 'unscheduled')
      const proposed = (await submit(owner, seed, 'propose_visit', 0, 'proposed', visitFields(at10, 60))).value
      checkVisit(proposed.proposed_visit, at10, 60, 'owner')
      check(proposed.confirmed_visit === null && !proposed.actions.includes('accept_visit')
        && await owner.panel.getByRole('button', { name: en.actions.accept_visit, exact: true }).count() === 0,
      'OWNER_CAN_ACCEPT_OWN_PROPOSAL')
      await owner.panel.getByText(en.timezone, { exact: true }).waitFor()

      winner = await openServices(browser, fixtureData, 'winner', 'es')
      const received = await openRequest(winner, seed, true)
      check(received.recipient.status === 'awarded' && received.project_id === seed.projectId && received.can_open_project, 'PROVIDER_AWARD_REQUIRED')
      await refreshWork(winner, seed, 1, 'proposed')
      const confirmed = (await submit(winner, seed, 'accept_visit', 1, 'confirmed')).value
      checkVisit(confirmed.confirmed_visit, at10, 60)
      check(confirmed.proposed_visit === null && !confirmed.actions.includes('request_completion'), 'EARLY_COMPLETION_AVAILABLE')
      await winner.panel.getByText(es.timezone, { exact: true }).waitFor()
      await mobileFits(winner.panel)
    })

    await step('reschedule_retains_then_replaces', async () => {
      const proposed = (await submit(winner, seed, 'propose_visit', 2, 'proposed', visitFields(at11, 75))).value
      checkVisit(proposed.confirmed_visit, at10, 60)
      checkVisit(proposed.proposed_visit, at11, 75, 'provider')
      const both = await refreshWork(owner, seed, 3, 'proposed')
      checkVisit(both.confirmed_visit, at10, 60)
      checkVisit(both.proposed_visit, at11, 75, 'provider')
      await owner.panel.getByText('Visit proposed by the provider', { exact: true }).waitFor()
      await owner.panel.getByText('Confirmed visit', { exact: true }).waitFor()
      const declined = (await submit(owner, seed, 'decline_visit', 3, 'confirmed')).value
      checkVisit(declined.confirmed_visit, at10, 60)
      check(declined.proposed_visit === null, 'DECLINED_RESCHEDULE_STILL_PENDING')

      await refreshWork(winner, seed, 4, 'confirmed')
      await submit(winner, seed, 'propose_visit', 4, 'proposed', visitFields(at11, 75))
      await refreshWork(owner, seed, 5, 'proposed')
      const moved = (await submit(owner, seed, 'accept_visit', 5, 'confirmed')).value
      checkVisit(moved.confirmed_visit, at11, 75)
      check(moved.proposed_visit === null, 'ACCEPTED_RESCHEDULE_STILL_PENDING')
    })

    await step('cancel_and_rebook', async () => {
      await refreshWork(winner, seed, 6, 'confirmed')
      const cancelled = (await submit(winner, seed, 'cancel_visit', 6, 'cancelled')).value
      check(cancelled.confirmed_visit === null && cancelled.proposed_visit === null, 'CANCELLED_VISIT_STILL_RESERVED')
      await refreshWork(owner, seed, 7, 'cancelled')
      await submit(owner, seed, 'propose_visit', 7, 'proposed', visitFields(at12, 60))
      await refreshWork(winner, seed, 8, 'proposed')
      const booked = (await submit(winner, seed, 'accept_visit', 8, 'confirmed')).value
      checkVisit(booked.confirmed_visit, at12, 60)
      check(!booked.actions.includes('request_completion')
        && await winner.panel.getByRole('button', { name: es.actions.request_completion, exact: true }).count() === 0,
      'PREVISIT_COMPLETION_CONTROL_VISIBLE')
    })

    await step('completion_review_private', async () => {
      const advance = await owner.context.request.post(`${API}${CLOCK_PATH}`, { data: {}, headers: { Origin: DASHBOARD },
        maxRedirects: 0, timeout: WAIT })
      try {
        const body = await advance.json()
        check(advance.status() === 200 && keys(body, ['advanced']) && body.advanced === true, 'SYNTHETIC_CLOCK_ADVANCE_FAILED')
        result.local_clock_advances += 1
      } finally {
        await advance.dispose()
      }
      await Promise.all([owner.page, winner.page].map(page => page.clock.setFixedTime(new Date(seed.clockAfter))))
      await refreshWork(winner, seed, 9, 'confirmed')
      const requested = (await submit(winner, seed, 'request_completion', 9, 'completion_requested', { note: INITIAL, completed_on: completedOn })).value
      check(requested.completion_note === INITIAL && requested.completed_on === completedOn && requested.completion_update_id === null,
        'PROVIDER_COMPLETION_RECEIPT_CHANGED')
      const before = await Promise.all([`${projectPath(seed)}/updates`, historyPath(seed)].map(path => read(owner.context, path)))
      check(before.every(payload => Array.isArray(payload.items) && payload.items.length === 0), 'UNAPPROVED_COMPLETION_IN_HISTORY')
      await refreshWork(owner, seed, 10, 'completion_requested')
      await owner.panel.getByText(INITIAL, { exact: true }).waitFor()
      const rework = (await submit(owner, seed, 'request_changes', 10, 'confirmed', { note: REVIEW })).value
      check(rework.completion_note === INITIAL && rework.completion_review_note === REVIEW && rework.completion_update_id === null,
        'REQUESTED_CHANGES_NOT_RETAINED_PRIVATELY')
      await refreshWork(winner, seed, 11, 'confirmed')
      await winner.panel.getByText(REVIEW, { exact: true }).waitFor()
      await submit(winner, seed, 'request_completion', 11, 'completion_requested', { note: APPROVED, completed_on: completedOn })
    })

    await step('owner_approval_and_history', async () => {
      await refreshWork(owner, seed, 12, 'completion_requested')
      await owner.panel.getByText(APPROVED, { exact: true }).waitFor()
      approval = await submit(owner, seed, 'approve_completion', 12, 'completed', { share_in_history: true })
      const updateId = approval.value.completion_update_id
      check(typeof updateId === 'string' && UUID.test(updateId) && approval.value.actions.length === 0
        && approval.value.completion_review_note === REVIEW, 'COMPLETED_APPROVAL_NOT_TERMINAL')
      const receiptPath = `${workPath(seed)}/submissions/${approval.submissionId}`
      const recovered = assertWork(await read(owner.context, receiptPath), seed, 13, 'completed')
      check(recovered.completion_update_id === updateId, 'OWNER_APPROVAL_RECEIPT_MISMATCH')
      await read(winner.context, receiptPath, 404)
      await refreshWork(winner, seed, 13, 'completed')
      await verifyHistory(winner, seed, updateId, completedOn)
      await verifyHistory(owner, seed, updateId, completedOn)
      result.final_work_version = 13
      result.exact_private_history_entries = 1
    })

    await step('loopback_only_and_no_automatic_writes', async () => {
      check(!externalNavigation && result.unexpected_browser_mutations === 0 && result.ui_work_posts === 13
        && submissionIds.size === 13 && result.local_clock_advances === 1, 'UNEXPECTED_BROWSER_NETWORK_EFFECT')
    })
  } finally {
    const closed = await Promise.allSettled([...contexts].map(context => close(context)))
    await browser.close()
    check(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
  result.status = 'PASS'
}

try {
  await main()
} catch (error) {
  result.failed_step = stage
  result.error_code = error instanceof TestFailure && /^[A-Z0-9_]+$/.test(error.message) ? error.message
    : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'UNEXPECTED_BROWSER_FAILURE'
  const frame = /run-service-visits-browser\.mjs:(\d{1,5}):\d+/.exec(String(error?.stack))
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`SERVICE_VISITS_BROWSER_RESULT ${JSON.stringify(result)}\n`)
