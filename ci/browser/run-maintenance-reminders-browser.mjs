import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const wait = 15_000
const roles = { owner: 'owner', winner: 'buyer', loser: 'buyer', outsider: 'buyer', admin: 'admin' }
const copy = {
  en: {
    title: 'Maintenance reminders', enable: 'Enable in-app reminders', lead: 'Days before due date',
    savePrefs: 'Save reminder preferences', scheduleTitle: 'Reminder title', firstDue: 'First due date',
    repeat: 'Repeat', add: 'Add reminder', complete: 'Mark complete', snooze: 'Snooze', pause: 'Pause',
    resume: 'Resume', stop: 'Stop reminder', snoozeUntil: 'Snooze until', saveSnooze: 'Save snooze',
    choose: 'Choose a property', maintenance: 'Maintenance', refresh: 'Refresh', archive: 'Archive private property',
  },
  es: {
    title: 'Recordatorios de mantenimiento', enable: 'Activar recordatorios en el panel', lead: 'Días antes del vencimiento',
    savePrefs: 'Guardar preferencias de recordatorios', scheduleTitle: 'Título del recordatorio', firstDue: 'Primera fecha de vencimiento',
    repeat: 'Repetir', add: 'Agregar recordatorio', complete: 'Marcar como completado', snooze: 'Posponer', pause: 'Pausar',
    resume: 'Reanudar', stop: 'Detener recordatorio', snoozeUntil: 'Posponer hasta', saveSnooze: 'Guardar aplazamiento',
    choose: 'Elige una propiedad', maintenance: 'Mantenimiento', refresh: 'Actualizar', archive: 'Archivar propiedad privada',
  },
}
const result = { status: 'FAIL', scope: 'isolated_synthetic_owner_maintenance_reminders', checks: [],
  preference_writes: 0, reminder_creates: 0, reminder_actions: 0, private_archive_writes: 0,
  unexpected_browser_mutations: 0, blocked_external_requests: 0 }
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
  require(hasKeys(value, ['accounts', 'maintenanceReminders']) && hasKeys(value.accounts, Object.keys(roles)), 'SYNTHETIC_ROLES_REQUIRED')
  for (const [role, account] of Object.entries(value.accounts)) {
    require(hasKeys(account, ['email', 'password']) && account.email === `browser-${role}@example.com`
      && typeof account.password === 'string' && account.password.length >= 24 && account.password.length <= 96,
    'EXACT_SYNTHETIC_LOGIN_REQUIRED')
  }
  const data = value.maintenanceReminders
  require(hasKeys(data, ['listing', 'private', 'dates']), 'TWO_MAINTENANCE_SOURCE_FIXTURES_REQUIRED')
  const privateFields = ['sourceKind','sourceId','projectId','title','version']
  const listingFields = ['sourceKind','sourceId','projectId','title']
  require(hasKeys(data.private, privateFields) && hasKeys(data.listing, listingFields)
    && [data.private.sourceId, data.private.projectId, data.listing.sourceId, data.listing.projectId].every(id => uuid.test(id))
    && data.private.sourceKind === 'private' && data.listing.sourceKind === 'listing'
    && data.private.title === 'Browser rehearsal private maintenance property' && data.private.version === 1
    && data.listing.title === 'Browser rehearsal villa', 'EXACT_SYNTHETIC_PROPERTY_IDENTITIES_REQUIRED')
  const fields = ['todayOn','privateFirstDueOn','privateSnoozeOn','privateNextDueOn','listingFirstDueOn']
  require(hasKeys(data.dates, fields) && fields.every(key => /^\d{4}-\d{2}-\d{2}$/.test(data.dates[key]))
    && data.dates.privateFirstDueOn < data.dates.todayOn && data.dates.privateSnoozeOn > data.dates.todayOn
    && data.dates.privateNextDueOn > data.dates.todayOn && data.dates.listingFirstDueOn === data.dates.todayOn,
  'EXACT_DOMINICAN_SYNTHETIC_DATES_REQUIRED')
  return value
}

const basePath = row => `/property-network/maintenance-reminders/${row.sourceKind}/${row.sourceId}`
const registryPath = row => `/property-network/registry/${row.sourceId}`
const privateTitle = 'Limpieza de piscina de prueba'
const listingTitle = 'Synthetic filter inspection'

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
  const context = await browser.newContext({ timezoneId: lang === 'es' ? 'Asia/Tokyo' : 'America/Los_Angeles', viewport: { width: 390, height: 844 }, locale: lang === 'es' ? 'es-DO' : 'en-US', serviceWorkers: 'block' })
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
      const scopes = [fixture.maintenanceReminders.private, fixture.maintenanceReminders.listing].map(basePath)
      const allowed = url.origin === api && role === 'owner'
      if (allowed && request.method() === 'PUT' && scopes.some(path => url.pathname === `${path}/preferences`)) result.preference_writes += 1
      else if (allowed && request.method() === 'POST' && scopes.includes(url.pathname)) result.reminder_creates += 1
      else if (allowed && request.method() === 'POST' && scopes.some(path =>
        url.pathname.startsWith(`${path}/`) && /^\/[0-9a-f-]{36}\/actions$/.test(url.pathname.slice(path.length)))) result.reminder_actions += 1
      else if (allowed && request.method() === 'PUT' && url.pathname === registryPath(fixture.maintenanceReminders.private)) result.private_archive_writes += 1
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

function reminderPanel(state) {
  return state.page.getByRole('region', { name: state.text.title, exact: true })
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}


async function navigate(state, fixture, path) {
  const { page, role } = state
  await page.bringToFront()
  const [auth] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}${path}`, { waitUntil: 'domcontentloaded' }),
  ])
  const user = await auth.json()
  require(auth.status() === 200 && user.role === roles[role] && user.email === fixture.accounts[role].email,
    'SIGNED_IN_ROLE_MISMATCH')
  await foreground(page)
}

async function openRegistry(state, fixture, row) {
  await navigate(state, fixture, `/property-network?tab=properties&property=${row.sourceId}`)
  await state.page.getByRole('heading', { name: row.title, exact: true, level: 1 }).waitFor()
}

async function openListing(state, fixture, row) {
  await navigate(state, fixture, '/property-management')
  await state.page.getByRole('combobox', { name: new RegExp(`^${state.text.choose}`) }).selectOption(row.sourceId)
  const tabs = state.page.getByRole('navigation').filter({ has: state.page.getByRole('button', { name: state.text.maintenance, exact: true }) })
  await tabs.getByRole('button', { name: state.text.maintenance, exact: true }).click()
  await reminderPanel(state).waitFor()
}

async function fits(panel) {
  await panel.scrollIntoViewIfNeeded()
  require(await panel.evaluate(element => {
    const rect = element.getBoundingClientRect()
    return innerWidth === 390 && rect.left >= -1 && rect.right <= 391 && document.documentElement.scrollWidth <= 391
  }), 'MOBILE_REMINDERS_OVERFLOW')
}

function expectPreference(value, enabled, lead, version) {
  require(hasKeys(value, ['enabled','lead_days','version']) && value.enabled === enabled
    && value.lead_days === lead && value.version === version, 'EXACT_REMINDER_PREFERENCE_REQUIRED')
}

function expectPage(value, scope, dates, count, enabled, lead, version) {
  require(hasKeys(value, ['source','as_of','time_zone','preference','items','total','page','page_size'])
    && hasKeys(value.source, ['kind','id']) && value.source.kind === scope.sourceKind && value.source.id === scope.sourceId
    && value.as_of === dates.todayOn && value.time_zone === 'America/Santo_Domingo' && value.page === 1 && value.page_size === 30
    && Array.isArray(value.items) && value.items.length === count && value.total === count,
  'EXACT_OWNER_SCOPED_REMINDER_PAGE_REQUIRED')
  expectPreference(value.preference, enabled, lead, version)
  return value.items[0]
}

function expectSchedule(row, title, repeat, anchor, version, status, attention, due, snoozed) {
  require(hasKeys(row, ['id','title','repeat','anchor_on','due_on','snoozed_until','status','version','attention'])
    && uuid.test(row.id) && row.title === title && row.repeat === repeat && row.anchor_on === anchor
    && row.version === version && row.status === status && row.attention === attention
    && (due === undefined || row.due_on === due) && (snoozed === undefined || row.snoozed_until === snoozed),
  'EXACT_SCHEDULE_LIFECYCLE_REQUIRED')
}

async function preference(state, scope, enabled, lead, prior) {
  const panel = reminderPanel(state)
  const toggle = panel.getByRole('checkbox', { name: state.text.enable, exact: true })
  const input = panel.getByLabel(state.text.lead, { exact: true })
  if (enabled) await toggle.check()
  if (await input.inputValue() !== String(lead)) await input.fill(String(lead))
  if (!enabled) await toggle.uncheck()
  const [response] = await Promise.all([
    state.page.waitForResponse(r => endpoint(r, `${basePath(scope)}/preferences`, 'PUT')),
    panel.getByRole('button', { name: state.text.savePrefs, exact: true }).click(),
  ])
  const body = response.request().postDataJSON()
  require(response.status() === 200 && privateHeaders(response)
    && hasKeys(body, ['submission_id','expected_version','enabled','lead_days'])
    && uuid.test(body.submission_id) && body.expected_version === prior && body.enabled === enabled && body.lead_days === lead,
  'EXPLICIT_REMINDER_PREFERENCE_WRITE_REQUIRED')
  expectPreference(await response.json(), enabled, lead, prior + 1)
  return body.submission_id
}

async function create(state, scope, title, due, repeat) {
  const panel = reminderPanel(state)
  await panel.getByLabel(state.text.scheduleTitle, { exact: true }).fill(title)
  await panel.getByLabel(state.text.firstDue, { exact: true }).fill(due)
  await panel.getByRole('combobox', { name: new RegExp(`^${state.text.repeat}`) }).selectOption(repeat)
  const [response] = await Promise.all([
    state.page.waitForResponse(r => endpoint(r, basePath(scope), 'POST')),
    panel.getByRole('button', { name: state.text.add, exact: true }).click(),
  ])
  const body = response.request().postDataJSON()
  require(response.status() === 201 && privateHeaders(response)
    && hasKeys(body, ['submission_id','title','repeat','first_due_on']) && uuid.test(body.submission_id)
    && body.title === title && body.repeat === repeat && body.first_due_on === due,
  'EXPLICIT_SCOPED_REMINDER_CREATE_REQUIRED')
  return { schedule: await response.json(), submission: body.submission_id }
}

function scheduleRow(state, title) {
  const panel = reminderPanel(state)
  return panel.getByRole('listitem').filter({ has: panel.getByRole('heading', { name: title, exact: true }) })
}

async function renderedDue(state, title, due) {
  const row = scheduleRow(state, title)
  const time = row.locator(`time[datetime="${due}"]`)
  await time.first().waitFor()
  const rendered = await time.first().innerText()
  const localized = await state.page.evaluate(({ due, locale }) => new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium', timeZone: 'America/Santo_Domingo',
  }).format(new Date(due + 'T12:00:00Z')), { due, locale: state.text === copy.es ? 'es-DO' : 'en-US' })
  require(rendered === localized, 'DOMINICAN_DATE_SHIFTED_IN_FOREIGN_BROWSER_TIME_ZONE')
  return row
}

async function action(state, scope, schedule, title, operation, snoozedUntil) {
  const row = scheduleRow(state, title)
  let button = row.getByRole('button', { name: state.text[operation], exact: true })
  if (operation === 'snooze') {
    await button.click()
    await reminderPanel(state).getByLabel(state.text.snoozeUntil, { exact: true }).fill(snoozedUntil)
    button = reminderPanel(state).getByRole('button', { name: state.text.saveSnooze, exact: true })
  }
  const [response] = await Promise.all([
    state.page.waitForResponse(r => endpoint(r, `${basePath(scope)}/${schedule.id}/actions`, 'POST')),
    button.click(),
  ])
  const body = response.request().postDataJSON()
  const keys = ['submission_id','expected_version','operation']
  if (operation === 'snooze') keys.push('snoozed_until')
  require(response.status() === 200 && privateHeaders(response) && hasKeys(body, keys)
    && uuid.test(body.submission_id) && body.expected_version === schedule.version && body.operation === operation
    && (operation !== 'snooze' || body.snoozed_until === snoozedUntil), 'EXPLICIT_VERSIONED_REMINDER_ACTION_REQUIRED')
  const saved = await response.json()
  require(saved.id === schedule.id, 'SCHEDULE_ID_CHANGED_ON_ACTION')
  return { schedule: saved, submission: body.submission_id }
}

function receiptPath(scope, submission) {
  return `${basePath(scope)}/submissions/${submission}`
}

async function step(name, run) {
  stage = name
  await run()
  result.checks.push(name)
}

async function main() {
  const fixture = await readFixture(), { private: registry, listing, dates } = fixture.maintenanceReminders
  const browser = await chromium.launch({ headless: true, timeout: 20_000,
    proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  let owner, listOwner, privateCreation, privateCompleted, listingCreation
  try {
    await step('current_owner_only_and_explicit_default_off', async () => {
      const guest = await session(browser, fixture, 'guest')
      await read(guest.context, basePath(registry), 401)
      await guest.context.close()
      for (const role of ['admin','winner','outsider']) {
        const state = await session(browser, fixture, role)
        if (role === 'admin') {
          let probes = 0
          state.page.on('request', request => { if (new URL(request.url()).pathname === basePath(registry)) probes += 1 })
          await openRegistry(state, fixture, registry)
          require(probes === 0 && await reminderPanel(state).count() === 0, 'ADMIN_REMINDER_PANEL_OR_PROBE')
        }
        await read(state.context, basePath(registry), 404)
        await read(state.context, basePath(listing), 404)
        await state.context.close()
      }
      owner = await session(browser, fixture, 'owner', 'es')
      await openRegistry(owner, fixture, registry)
      await reminderPanel(owner).waitFor()
      expectPage(await read(owner.context, basePath(registry)), registry, dates, 0, false, 7, 0)
      const toggle = reminderPanel(owner).getByRole('checkbox', { name: owner.text.enable, exact: true })
      require(!await toggle.isChecked(), 'REGISTRY_REMINDERS_ENABLED_BEFORE_CONSENT')
      await fits(reminderPanel(owner))

      listOwner = await session(browser, fixture, 'owner')
      await openListing(listOwner, fixture, listing)
      expectPage(await read(listOwner.context, basePath(listing)), listing, dates, 0, false, 7, 0)
      const listingToggle = reminderPanel(listOwner).getByRole('checkbox', { name: listOwner.text.enable, exact: true })
      require(!await listingToggle.isChecked() && result.preference_writes === 0 && result.reminder_creates === 0,
        'LISTING_REMINDERS_ENABLED_BEFORE_CONSENT')
      await fits(reminderPanel(listOwner))
    })
    await step('spanish_mobile_registry_monthly_snooze_advance_and_stop', async () => {
      await foreground(owner.page)
      await preference(owner, registry, true, 3, 0)
      privateCreation = await create(owner, registry, privateTitle, dates.privateFirstDueOn, 'monthly')
      const first = privateCreation.schedule
      expectSchedule(first, privateTitle, 'monthly', dates.privateFirstDueOn, 1, 'active', 'overdue', dates.privateFirstDueOn, null)
      await renderedDue(owner, privateTitle, dates.privateFirstDueOn)
      const snoozed = await action(owner, registry, first, privateTitle, 'snooze', dates.privateSnoozeOn)
      expectSchedule(snoozed.schedule, privateTitle, 'monthly', dates.privateFirstDueOn, 2, 'active', 'snoozed',
        dates.privateFirstDueOn, dates.privateSnoozeOn)
      await renderedDue(owner, privateTitle, dates.privateFirstDueOn)
      privateCompleted = await action(owner, registry, snoozed.schedule, privateTitle, 'complete')
      const advanced = privateCompleted.schedule
      require(['upcoming','scheduled'].includes(advanced.attention), 'ADVANCED_SCHEDULE_HAS_WRONG_ATTENTION')
      expectSchedule(advanced, privateTitle, 'monthly', dates.privateFirstDueOn, 3, 'active', advanced.attention, dates.privateNextDueOn, null)
      await renderedDue(owner, privateTitle, dates.privateNextDueOn)
      const paused = await action(owner, registry, advanced, privateTitle, 'pause')
      expectSchedule(paused.schedule, privateTitle, 'monthly', dates.privateFirstDueOn, 4, 'paused', 'none', dates.privateNextDueOn)
      const resumed = await action(owner, registry, paused.schedule, privateTitle, 'resume')
      expectSchedule(resumed.schedule, privateTitle, 'monthly', dates.privateFirstDueOn, 5, 'active', advanced.attention, dates.privateNextDueOn)
      const stopped = await action(owner, registry, resumed.schedule, privateTitle, 'stop')
      expectSchedule(stopped.schedule, privateTitle, 'monthly', dates.privateFirstDueOn, 6, 'stopped', 'none', dates.privateNextDueOn, null)
      const saved = expectPage(await read(owner.context, basePath(registry)), registry, dates, 1, true, 3, 1)
      require(saved.id === first.id && saved.status === 'stopped' && saved.version === 6, 'STOPPED_SCHEDULE_NOT_RETAINED')
      const receipt = await read(owner.context, receiptPath(registry, privateCompleted.submission))
      require(hasKeys(receipt, ['operation','result']) && receipt.operation === 'complete'
        && receipt.result.id === first.id && receipt.result.version === 6, 'RECEIPT_DID_NOT_RESOLVE_CURRENT_ACTOR_SCHEDULE')
      await fits(reminderPanel(owner))
    })
    await step('english_mobile_listing_one_time_due_and_disable', async () => {
      await foreground(listOwner.page)
      await preference(listOwner, listing, true, 5, 0)
      listingCreation = await create(listOwner, listing, listingTitle, dates.listingFirstDueOn, 'none')
      expectSchedule(listingCreation.schedule, listingTitle, 'none', dates.listingFirstDueOn, 1, 'active', 'due',
        dates.listingFirstDueOn, null)
      await renderedDue(listOwner, listingTitle, dates.listingFirstDueOn)
      const completed = await action(listOwner, listing, listingCreation.schedule, listingTitle, 'complete')
      expectSchedule(completed.schedule, listingTitle, 'none', dates.listingFirstDueOn, 2, 'completed', 'none', dates.listingFirstDueOn, null)
      await preference(listOwner, listing, false, 5, 1)
      const saved = expectPage(await read(listOwner.context, basePath(listing)), listing, dates, 1, false, 5, 2)
      require(saved.id === listingCreation.schedule.id && saved.status === 'completed'
        && saved.id !== privateCreation.schedule.id, 'LISTING_AND_PRIVATE_WORKSPACES_MIXED')
      await read(listOwner.context, receiptPath(listing, privateCreation.submission), 404)
      await read(owner.context, receiptPath(registry, listingCreation.submission), 404)
      await fits(reminderPanel(listOwner))
    })
    await step('registry_archive_clears_access_and_keeps_listing_separate', async () => {
      const archiveState = { ...owner, page: await owner.context.newPage() }
      await openRegistry(archiveState, fixture, registry)
      const confirmed = archiveState.page.waitForEvent('dialog').then(async dialog => {
        require(dialog.type() === 'confirm', 'PRIVATE_ARCHIVE_CONFIRMATION_REQUIRED')
        await dialog.accept()
      })
      const [archived] = await Promise.all([
        archiveState.page.waitForResponse(r => endpoint(r, registryPath(registry), 'PUT')),
        confirmed,
        archiveState.page.getByRole('button', { name: owner.text.archive, exact: true }).click(),
      ])
      const body = archived.request().postDataJSON()
      require(archived.status() === 200 && privateHeaders(archived) && body.status === 'archived'
        && body.expected_version === registry.version && (await archived.json()).status === 'archived',
      'REAL_SYNTHETIC_PRIVATE_ARCHIVE_REQUIRED')
      await archiveState.page.close()
      await read(owner.context, basePath(registry), 404)
      await read(owner.context, receiptPath(registry, privateCreation.submission), 404)
      await foreground(owner.page)
      const inaccessible = owner.page.getByRole('heading', { name: privateTitle, exact: true }).waitFor({ state: 'detached' })
      const refresh = reminderPanel(owner).getByRole('button', { name: owner.text.refresh, exact: true })
      if (await refresh.isVisible()) await Promise.race([inaccessible, refresh.click()])
      await inaccessible
      expectPage(await read(listOwner.context, basePath(listing)), listing, dates, 1, false, 5, 2)
    })
    await step('loopback_only_no_background_or_unrelated_mutations', async () => {
      require(!externalNavigation && result.preference_writes === 3 && result.reminder_creates === 2
        && result.reminder_actions === 6 && result.private_archive_writes === 1 && result.unexpected_browser_mutations === 0,
      'UNEXPECTED_BROWSER_NETWORK_EFFECT')
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
  const frames = [...String(error?.stack).matchAll(/run-maintenance-reminders-browser\.mjs:(\d{1,5}):\d+/g)]
  const frame = error instanceof RehearsalFailure && frames.length > 1 ? frames[1] : frames[0]
  if (frame) result.source_line = Number(frame[1])
  process.exitCode = 1
}
process.stdout.write(`MAINTENANCE_REMINDERS_BROWSER_RESULT ${JSON.stringify(result)}\n`)
