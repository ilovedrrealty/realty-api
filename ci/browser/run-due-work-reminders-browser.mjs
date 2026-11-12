import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const copy = {
  en: {
    title: 'Due-work reminders', milestones: 'Remind me about milestones', visits: 'Remind me about confirmed visits',
    lead: 'Days before due date', save: 'Save reminder preferences', refresh: 'Refresh', dismiss: 'Dismiss',
    restore: 'Restore reminder', overdue: 'Overdue', upcoming: 'Upcoming', dismissed: 'Dismissed',
    visitTime: 'Visit time', timeline: 'Progress timeline', serviceNav: 'Private service workflow', received: 'Requests for me',
    openRequest: 'Open request', work: 'Service visit and completion', accept: 'Accept proposed visit', confirm: 'Confirm action',
  },
  es: {
    title: 'Recordatorios de trabajo pendiente', milestones: 'Recordarme los hitos', visits: 'Recordarme las visitas confirmadas',
    lead: 'Días antes del vencimiento', save: 'Guardar preferencias de recordatorios', refresh: 'Actualizar', snooze: 'Posponer',
    snoozeUntil: 'Posponer hasta', saveSnooze: 'Guardar aplazamiento', restore: 'Restaurar recordatorio',
    overdue: 'Vencido', upcoming: 'Próximo', snoozed: 'Pospuesto', visitTime: 'Hora de la visita', timeline: 'Cronología del progreso',
    serviceNav: 'Flujo privado de servicios', openRequest: 'Abrir solicitud', work: 'Visita de servicio y aprobación del trabajo',
    propose: 'Proponer una fecha y hora', start: 'Fecha y hora de inicio (Santo Domingo)', duration: 'Duración en minutos (15–480)',
    confirm: 'Confirmar acción', team: 'Equipo del proyecto', revoke: 'Revocar acceso al proyecto',
    memberCode: 'Código de miembro', memberRole: 'Rol en el proyecto', grant: 'Conceder acceso al proyecto',
  },
}

const result = { status: 'FAIL', scope: 'isolated_synthetic_project_due_work_reminders', checks: [],
  preference_writes: 0, reminder_actions: 0, visit_writes: 0, membership_writes: 0,
  unexpected_browser_mutations: 0, blocked_external_requests: 0 }
const contexts = []
let stage = 'fixture'
let externalNavigation = false

class RehearsalFailure extends Error {}
function require(condition, code) {
  if (!condition) throw new RehearsalFailure(code)
}
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const date = /^\d{4}-\d{2}-\d{2}$/
function keys(value, names) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name))
}

async function readFixture() {
  require(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'EXACT_HOSTED_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  require(file.isFile() && (file.mode & 0o777) === 0o600 && file.size > 0 && file.size <= 12_000, 'PRIVATE_SYNTHETIC_FILE_REQUIRED')
  const value = JSON.parse(await readFile(fixturePath, 'utf8'))
  require(keys(value, ['accounts', 'dueWork']) && keys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(keys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const f = value.dueWork
  require(keys(f, ['projectId', 'projectTitle', 'projectVersion', 'requestId', 'requestTitle', 'milestoneId', 'milestoneTitle',
    'winnerUserCode', 'otherProfessionalUserCode', 'viewerUserCode', 'durationMinutes', 'dates'])
    && [f.projectId, f.requestId, f.milestoneId].every(id => uuid.test(id))
    && [f.projectVersion, f.winnerUserCode, f.otherProfessionalUserCode, f.viewerUserCode].every(n => Number.isInteger(n) && n > 0)
    && new Set([f.winnerUserCode, f.otherProfessionalUserCode, f.viewerUserCode]).size === 3 && f.durationMinutes === 60
    && f.projectTitle === 'Browser rehearsal villa service project' && f.requestTitle === 'Browser rehearsal awarded pool service'
    && f.milestoneTitle === 'Synthetic pool readiness milestone', 'EXACT_SYNTHETIC_PROJECT_REQUIRED')
  const d = f.dates
  require(keys(d, ['todayOn', 'milestoneDueOn', 'snoozeOn', 'initialVisitStart', 'initialVisitDueOn', 'rescheduledVisitStart', 'rescheduledVisitDueOn'])
    && [d.todayOn, d.milestoneDueOn, d.snoozeOn, d.initialVisitDueOn, d.rescheduledVisitDueOn].every(v => date.test(v))
    && d.milestoneDueOn < d.todayOn && d.todayOn < d.initialVisitDueOn && d.initialVisitDueOn < d.rescheduledVisitDueOn
    && d.snoozeOn === d.rescheduledVisitDueOn && d.initialVisitStart === `${d.initialVisitDueOn}T10:00:00-04:00`
    && d.rescheduledVisitStart === `${d.rescheduledVisitDueOn}T11:00:00-04:00`, 'EXACT_DOMINICAN_SYNTHETIC_DATES_REQUIRED')
  return value
}

const projectPath = f => `/property-network/projects/${f.projectId}`
const reminders = f => `${projectPath(f)}/due-work-reminders`
const workPath = f => `/property-network/service-requests/${f.requestId}/work`
const receiptPath = (f, id) => `${reminders(f)}/submissions/${id}`
const actionPath = (f, kind) => `${reminders(f)}/${kind}/${kind === 'milestone' ? f.milestoneId : f.requestId}/actions`
const membersPath = f => `${projectPath(f)}/members`
const revokePath = f => `${membersPath(f)}/${f.winnerUserCode}/revoke`

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
    if (status >= 400) require(keys(value, ['detail']), 'PRIVATE_DENIAL_EXPOSED_DATA')
    return value
  } finally { await response.dispose() }
}

function allowedWrite(url, method, role, f) {
  if (url.origin !== api) return null
  const path = url.pathname
  if (method === 'PUT' && ['owner', 'winner', 'loser'].includes(role) && path === `${reminders(f)}/preferences`) return 'preference_writes'
  if (method !== 'POST') return null
  if ((role === 'owner' && path === actionPath(f, 'milestone')) || (role === 'winner' && path === actionPath(f, 'visit'))) return 'reminder_actions'
  if (['owner', 'winner'].includes(role) && path === workPath(f)) return 'visit_writes'
  if (role === 'owner' && [revokePath(f), membersPath(f)].includes(path)) return 'membership_writes'
  return null
}

async function session(browser, fixture, role, lang = 'en') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    timezoneId: lang === 'es' ? 'Asia/Tokyo' : 'America/Los_Angeles', locale: lang === 'es' ? 'es-DO' : 'en-US' })
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
      const counter = allowedWrite(url, request.method(), role, fixture.dueWork)
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
    const login = await context.request.post(`${api}/auth/login`, { data: account,
      headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
      require(login.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'REAL_SYNTHETIC_AUTH_REQUIRED')
    } finally { await login.dispose() }
  }
  return { context, page: account ? await context.newPage() : null, role, account, t: copy[lang], lang }
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const heading = (scope, name, level) => scope.getByRole('heading', { name, exact: true, level })
const panel = state => state.page.getByRole('region', { name: state.t.title, exact: true })
const row = (state, title) => panel(state).getByRole('listitem').filter({ has: heading(state.page, title, 3) })

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
  require(await page.evaluate(lang => !document.cookie.includes('ildr_token=') && localStorage.getItem('ildr_lang') === lang, state.lang),
    'ISOLATED_BROWSER_CONTEXT_INVALID')
}

async function openProject(state, f) {
  await navigate(state, `/property-network?tab=projects&project=${f.projectId}`)
  await heading(state.page, f.projectTitle, 1).waitFor()
  await button(state.page, state.t.timeline).waitFor()
}

async function mobileFits(locator) {
  await locator.scrollIntoViewIfNeeded()
  require(await locator.evaluate(element => {
    const box = element.getBoundingClientRect()
    return innerWidth === 390 && box.left >= -1 && box.right <= innerWidth + 1
      && document.documentElement.scrollWidth <= innerWidth + 1
  }), 'MOBILE_HORIZONTAL_OVERFLOW')
}

function assertPreferences(value, enabled, version) {
  require(keys(value, ['milestones_enabled', 'visits_enabled', 'lead_days', 'version'])
    && value.milestones_enabled === enabled && value.visits_enabled === enabled && value.lead_days === 7 && value.version === version,
  'EXACT_ACTOR_PREFERENCES_REQUIRED')
}

async function snapshot(state, f, enabled, count) {
  const value = await read(state.context, reminders(f))
  require(keys(value, ['project_id', 'as_of', 'time_zone', 'preference', 'items', 'total', 'page', 'page_size'])
    && value.project_id === f.projectId && value.as_of === f.dates.todayOn && value.time_zone === 'America/Santo_Domingo'
    && value.page === 1 && value.page_size === 30 && value.total === count && Array.isArray(value.items) && value.items.length === count,
  'EXACT_PROJECT_PROJECTION_REQUIRED')
  assertPreferences(value.preference, enabled, enabled ? 1 : 0)
  return value.items
}

function itemIs(item, f, kind, occurrence, state = 'active') {
  const visit = kind === 'visit', moved = occurrence === 4, d = f.dates
  const due = visit ? (moved ? d.rescheduledVisitDueOn : d.initialVisitDueOn) : d.milestoneDueOn
  const starts = moved ? d.rescheduledVisitStart : d.initialVisitStart
  require(keys(item, ['kind', 'id', 'occurrence_version', 'title', 'due_on', 'starts_at', 'duration_minutes',
    'state', 'snoozed_until', 'version', 'attention']) && item.kind === kind && item.id === (visit ? f.requestId : f.milestoneId)
    && item.title === (visit ? f.requestTitle : f.milestoneTitle) && item.due_on === due
    && (visit ? Date.parse(item.starts_at) === Date.parse(starts) : item.starts_at === null)
    && item.duration_minutes === (visit ? f.durationMinutes : null) && item.occurrence_version === occurrence
    && item.state === state && item.version === (state === 'active' ? 0 : 1)
    && item.snoozed_until === (state === 'snoozed' ? d.snoozeOn : null)
    && item.attention === (state === 'active' ? (visit ? 'upcoming' : 'overdue') : 'none'), 'EXACT_OCCURRENCE_PROJECTION_REQUIRED')
}

async function assertItems(state, f, specs) {
  const items = await snapshot(state, f, true, specs.length)
  for (const [kind, occurrence, disposition] of specs) itemIs(items.find(item => item.kind === kind), f, kind, occurrence, disposition)
}

async function defaultOptOut(state, f) {
  await foreground(state.page)
  const region = panel(state), milestones = region.getByLabel(state.t.milestones, { exact: true }), visits = region.getByLabel(state.t.visits, { exact: true })
  await milestones.waitFor()
  await visits.waitFor()
  require(!await milestones.isChecked() && !await visits.isChecked()
    && await region.getByLabel(state.t.lead, { exact: true }).inputValue() === '7'
    && await button(region, state.t.save).isDisabled() && await region.getByRole('listitem').count() === 0,
  'DUE_WORK_REQUIRES_EXPLICIT_OPT_IN')
  await snapshot(state, f, false, 0)
  await mobileFits(region)
}

async function mutation(state, path, method, target, expected, idempotent = true) {
  const [response] = await Promise.all([
    state.page.waitForResponse(response => endpoint(response, path, method)),
    target.click(),
  ])
  require(response.status() === 200 && privateHeaders(response), 'EXACT_PRIVATE_MUTATION_FAILED')
  const body = response.request().postDataJSON()
  require(keys(body, [...Object.keys(expected), ...(idempotent ? ['submission_id'] : [])])
    && Object.entries(expected).every(([key, value]) => body[key] === value)
    && (!idempotent || uuid.test(body.submission_id)), 'EXACT_SYNTHETIC_MUTATION_BODY_REQUIRED')
  return { value: await response.json(), key: body.submission_id }
}

async function enable(state, f) {
  await foreground(state.page)
  const region = panel(state)
  await region.getByLabel(state.t.milestones, { exact: true }).check()
  await region.getByLabel(state.t.visits, { exact: true }).check()
  const saved = await mutation(state, `${reminders(f)}/preferences`, 'PUT', button(region, state.t.save),
    { expected_version: 0, milestones_enabled: true, visits_enabled: true, lead_days: 7 })
  assertPreferences(saved.value, true, 1)
  return saved.key
}

async function receipt(state, f, key, status, operation) {
  const value = await read(state.context, receiptPath(f, key), status)
  if (status === 200) require(keys(value, ['operation', 'result']) && value.operation === operation, 'RECEIPT_OPERATION_MISMATCH')
  return value.result
}

async function displayVisit(state, f, moved) {
  await foreground(state.page)
  const visit = row(state, f.requestTitle)
  const starts = moved ? f.dates.rescheduledVisitStart : f.dates.initialVisitStart
  await visit.getByText(state.t.visitTime, { exact: true }).waitFor()
  await state.page.waitForFunction(({ title, starts }) => [...document.querySelectorAll('li')].some(item =>
    item.querySelector('h3')?.textContent === title && Date.parse(item.querySelector('time')?.dateTime) === Date.parse(starts)),
  { title: f.requestTitle, starts })
  const locale = state.lang === 'es' ? 'es-DO' : 'en-US'
  require(await visit.locator('time').evaluate((element, { starts, locale }) => element.textContent === new Intl.DateTimeFormat(locale,
    { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Santo_Domingo' }).format(new Date(starts)), { starts, locale }),
  'DISPLAYED_VISIT_TIME_MUST_USE_SANTO_DOMINGO')
  await mobileFits(visit)
}

async function refreshReminders(state, f) {
  await foreground(state.page)
  const [response] = await Promise.all([
    state.page.waitForResponse(response => endpoint(response, reminders(f))),
    button(panel(state), state.t.refresh).click(),
  ])
  require(response.status() === 200 && privateHeaders(response), 'BROWSER_REMINDERS_REFRESH_FAILED')
}

async function snooze(owner, f) {
  await foreground(owner.page)
  const milestone = row(owner, f.milestoneTitle)
  await milestone.getByText(owner.t.overdue, { exact: true }).waitFor()
  await button(milestone, owner.t.snooze).click()
  await milestone.getByLabel(owner.t.snoozeUntil, { exact: true }).fill(f.dates.snoozeOn)
  const saved = await mutation(owner, actionPath(f, 'milestone'), 'POST', button(milestone, owner.t.saveSnooze),
    { expected_version: 0, occurrence_version: 1, operation: 'snooze', snoozed_until: f.dates.snoozeOn })
  itemIs(saved.value, f, 'milestone', 1, 'snoozed')
  await milestone.getByText(owner.t.snoozed, { exact: true }).waitFor()
  await button(milestone, owner.t.restore).waitFor()
  return saved.key
}

async function dismiss(winner, f, occurrence) {
  await foreground(winner.page)
  const visit = row(winner, f.requestTitle)
  await visit.getByText(winner.t.upcoming, { exact: true }).waitFor()
  const saved = await mutation(winner, actionPath(f, 'visit'), 'POST', button(visit, winner.t.dismiss),
    { expected_version: 0, occurrence_version: occurrence, operation: 'dismiss' })
  itemIs(saved.value, f, 'visit', occurrence, 'dismissed')
  await visit.getByText(winner.t.dismissed, { exact: true }).waitFor()
  await button(visit, winner.t.restore).waitFor()
  return saved.key
}

async function openWork(state, f, received) {
  const work = { ...state, page: await state.context.newPage() }
  await navigate(work, '/property-network?tab=services')
  const nav = work.page.getByRole('navigation', { name: work.t.serviceNav, exact: true })
  await nav.waitFor()
  if (received) await button(nav, work.t.received).click()
  const article = work.page.locator('main article').filter({ has: heading(work.page, f.requestTitle) })
  await button(article, work.t.openRequest).click()
  work.section = work.page.locator('section').filter({ has: heading(work.page, work.t.work, 2) })
  await work.section.waitFor()
  return work
}

function assertVisitWork(value, f, phase) {
  const proposed = phase === 'proposed', version = proposed ? 3 : 4
  const confirmed = proposed ? f.dates.initialVisitStart : f.dates.rescheduledVisitStart
  require(value.request_id === f.requestId && value.project_id === f.projectId && value.version === version && value.status === phase
    && Date.parse(value.confirmed_visit?.starts_at) === Date.parse(confirmed) && value.confirmed_visit.duration_minutes === f.durationMinutes
    && (proposed ? Date.parse(value.proposed_visit?.starts_at) === Date.parse(f.dates.rescheduledVisitStart)
      && value.proposed_visit.duration_minutes === f.durationMinutes && value.proposed_visit.proposed_by === 'owner' : value.proposed_visit === null),
  'EXACT_CONFIRMED_AND_PROPOSED_SLOT_REQUIRED')
}

async function step(name, test) {
  stage = name
  await test()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), f = fixture.dueWork
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, winner, other, winnerPreference, ownerSnooze, oldDismissal, currentDismissal
  try {
    await step('active_roles_and_default_opt_out', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, reminders(f), 401)
      await guest.context.close()
      for (const role of ['admin', 'outsider']) {
        const state = await session(browser, fixture, role)
        let probes = 0
        state.page.on('request', request => { if (new URL(request.url()).pathname === reminders(f)) probes += 1 })
        await openProject(state, f)
        await read(state.context, reminders(f), 404)
        require(await panel(state).count() === 0 && probes === 0, 'ADMIN_OR_VIEWER_REMINDER_PROBE')
        await state.context.close()
      }
      owner = await session(browser, fixture, 'owner', 'es')
      winner = await session(browser, fixture, 'winner')
      other = await session(browser, fixture, 'loser')
      for (const state of [owner, winner, other]) {
        await openProject(state, f)
        await defaultOptOut(state, f)
      }
      require(result.preference_writes === 0 && result.reminder_actions === 0, 'PROJECT_OPEN_MUST_NOT_MUTATE_REMINDERS')
    })

    await step('private_consent_snooze_and_dismiss', async () => {
      await enable(owner, f)
      winnerPreference = await enable(winner, f)
      await enable(other, f)
      await assertItems(other, f, [['milestone', 1]])
      await row(other, f.milestoneTitle).getByText(other.t.overdue, { exact: true }).waitFor()
      require(await row(other, f.requestTitle).count() === 0, 'OTHER_PROFESSIONAL_SEES_PRIVATE_VISIT')
      await displayVisit(owner, f, false)
      await displayVisit(winner, f, false)
      ownerSnooze = await snooze(owner, f)
      oldDismissal = await dismiss(winner, f, 2)
      await assertItems(owner, f, [['milestone', 1, 'snoozed'], ['visit', 2]])
      await assertItems(winner, f, [['milestone', 1], ['visit', 2, 'dismissed']])
      await assertItems(other, f, [['milestone', 1]])
      itemIs(await receipt(owner, f, ownerSnooze, 200, 'snooze'), f, 'milestone', 1, 'snoozed')
      itemIs(await receipt(winner, f, oldDismissal, 200, 'dismiss'), f, 'visit', 2, 'dismissed')
      await receipt(owner, f, oldDismissal, 404)
      await receipt(winner, f, ownerSnooze, 404)
      await receipt(other, f, winnerPreference, 404)
    })

    await step('pending_reschedule_preserves_confirmed_occurrence', async () => {
      const work = await openWork(owner, f, false)
      await button(work.section, work.t.propose).click()
      const form = work.section.locator('form')
      await form.getByLabel(work.t.start, { exact: true }).fill(f.dates.rescheduledVisitStart.slice(0, 16))
      await form.getByLabel(work.t.duration, { exact: true }).fill(String(f.durationMinutes))
      const written = await mutation(work, workPath(f), 'POST', button(form, work.t.confirm),
        { expected_version: 2, operation: 'propose_visit', starts_at: f.dates.rescheduledVisitStart, duration_minutes: f.durationMinutes })
      assertVisitWork(written.value, f, 'proposed')
      await work.page.close()
      await assertItems(owner, f, [['milestone', 1, 'snoozed'], ['visit', 2]])
      await assertItems(winner, f, [['milestone', 1], ['visit', 2, 'dismissed']])
      itemIs(await receipt(winner, f, oldDismissal, 200, 'dismiss'), f, 'visit', 2, 'dismissed')
      await refreshReminders(winner, f)
      await displayVisit(winner, f, false)
      await row(winner, f.requestTitle).getByText(winner.t.dismissed, { exact: true }).waitFor()
    })

    await step('accepted_reschedule_creates_new_occurrence', async () => {
      const work = await openWork(winner, f, true)
      await button(work.section, work.t.accept).click()
      const accepted = await mutation(work, workPath(f), 'POST', button(work.section.locator('form'), work.t.confirm),
        { expected_version: 3, operation: 'accept_visit' })
      assertVisitWork(accepted.value, f, 'confirmed')
      await work.page.close()
      await assertItems(owner, f, [['milestone', 1, 'snoozed'], ['visit', 4]])
      await assertItems(winner, f, [['milestone', 1], ['visit', 4]])
      await receipt(winner, f, oldDismissal, 404)
      await refreshReminders(owner, f)
      await displayVisit(owner, f, true)
      await refreshReminders(winner, f)
      await displayVisit(winner, f, true)
      currentDismissal = await dismiss(winner, f, 4)
      await assertItems(owner, f, [['milestone', 1, 'snoozed'], ['visit', 4]])
      await assertItems(winner, f, [['milestone', 1], ['visit', 4, 'dismissed']])
    })

    await step('revocation_regrant_resets_consent_and_receipts', async () => {
      await foreground(owner.page)
      await button(owner.page, owner.t.team).click()
      const team = owner.page.locator('section').filter({ has: heading(owner.page, owner.t.team, 2) })
      const winnerRow = team.getByRole('listitem').filter({ hasText: `#${f.winnerUserCode} ·` })
      const revoked = await mutation(owner, revokePath(f), 'POST', button(winnerRow, owner.t.revoke),
        { expected_version: f.projectVersion }, false)
      require(Array.isArray(revoked.value), 'PROJECT_REVOKE_RESULT_CHANGED')
      await read(winner.context, reminders(f), 404)
      await receipt(winner, f, winnerPreference, 404)
      await receipt(winner, f, currentDismissal, 404)
      await foreground(winner.page)
      const [project] = await Promise.all([
        winner.page.waitForResponse(response => endpoint(response, projectPath(f)) && response.status() === 404),
        winner.page.reload({ waitUntil: 'domcontentloaded' }),
      ])
      require(privateHeaders(project), 'REVOKED_PROJECT_MUST_BE_PRIVATE')
      await panel(winner).waitFor({ state: 'detached' })
      await heading(winner.page, f.projectTitle, 1).waitFor({ state: 'detached' })

      await openProject(owner, f)
      await button(owner.page, owner.t.team).click()
      const grantForm = team.locator('form')
      await grantForm.getByLabel(owner.t.memberCode, { exact: true }).fill(String(f.winnerUserCode))
      const roleSelect = grantForm.locator('select')
      // The wrapped label also contains option text. Check its direct label separately.
      require(await roleSelect.count() === 1 && await roleSelect.evaluate((select, expectedLabel) =>
        [...select.labels].some(label => [...label.childNodes].filter(node => node.nodeType === Node.TEXT_NODE)
          .map(node => node.textContent).join('').trim() === expectedLabel)
        && [...select.options].filter(option => option.value === 'professional').length === 1, owner.t.memberRole),
      'PROJECT_ROLE_FIELD_OR_LABEL_CHANGED')
      await roleSelect.selectOption('professional')
      const granted = await mutation(owner, membersPath(f), 'POST', button(grantForm, owner.t.grant),
        { expected_version: f.projectVersion + 1, user_code: f.winnerUserCode, role: 'professional' }, false)
      require(Array.isArray(granted.value) && granted.value.some(member => member.user_code === f.winnerUserCode
        && member.role === 'professional' && member.active === true), 'ACTIVE_PROFESSIONAL_REGRANT_REQUIRED')
      await openProject(winner, f)
      await defaultOptOut(winner, f)
      for (const old of [winnerPreference, oldDismissal, currentDismissal]) await receipt(winner, f, old, 404)
      const renewedPreference = await enable(winner, f)
      require(renewedPreference !== winnerPreference, 'REGRANT_REUSED_OLD_CONSENT')
      assertPreferences(await receipt(winner, f, renewedPreference, 200, 'preferences'), true, 1)
      await assertItems(winner, f, [['milestone', 1], ['visit', 4]])
      await assertItems(owner, f, [['milestone', 1, 'snoozed'], ['visit', 4]])
      await assertItems(other, f, [['milestone', 1]])
      await displayVisit(winner, f, true)
    })

    await step('loopback_only_exact_synthetic_mutations', async () => {
      require(result.preference_writes === 4 && result.reminder_actions === 3 && result.visit_writes === 2
        && result.membership_writes === 2 && result.unexpected_browser_mutations === 0
        && result.blocked_external_requests <= 100 && !externalNavigation, 'EXACT_LOOPBACK_MUTATIONS_REQUIRED')
    })
    result.status = 'PASS'
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    await browser.close()
    require(!browser.isConnected() && closed.every(entry => entry.status === 'fulfilled'), 'BROWSER_CLEANUP_FAILED')
  }
}

try {
  await main()
} catch (error) {
  result.status = 'FAIL'
  result.failed_step = stage
  result.error_code = error instanceof RehearsalFailure ? error.message : error?.name === 'TimeoutError' ? 'BROWSER_TIMEOUT' : 'BROWSER_FAILURE'
  const frames = [...String(error?.stack ?? '').matchAll(/run-due-work-reminders-browser\.mjs:(\d+):\d+/g)]
  const source = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  result.source_line = source ? Number(source[1]) : null
  process.exitCode = 1
}
console.log('DUE_WORK_REMINDERS_BROWSER_RESULT ' + JSON.stringify(result))
