import { createHash } from 'node:crypto'
import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175', api = 'http://127.0.0.1:8000'
const network = '/property-network', wait = 15_000
const roles = { owner: 'owner', professional: 'buyer', outsider: 'buyer' }
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const copy = {
  en: { add: 'Add a private property', name: 'Private property name', location: 'Town or area',
    address: 'Private address (optional)', class: 'Property class', relationship: 'Your relationship to this property',
    notes: 'Private notes (optional)', authority: 'I confirm that I am authorized to create and manage this private property workspace. This does not verify ownership or publish a listing.',
    register: 'Save private property', createFor: 'Create a project for this property', create: 'Create a private project',
    title: 'Project title', summary: 'Project summary', services: 'Services involved (up to eight)',
    tabs: 'Project sections', timeline: 'Progress timeline', members: 'Project team', milestones: 'Milestones',
    milestonePanel: 'Project milestones', milestone: 'Milestone', due: 'Due date (optional)', addMilestone: 'Add milestone',
    status: 'Status', save: 'Save changes', code: 'Member code', role: 'Project role', grant: 'Grant project access',
    revoke: 'Revoke project access', message: 'Message', attach: 'Add private progress photos', submit: 'Save entry',
    photo: 'Private photo attached to this entry', comments: 'View comments', context: 'This comment is about',
    history: 'Property service history', reload: 'Reload current record', denied: 'This record is unavailable or your current account no longer has access.',
  },
  es: { tabs: 'Secciones del proyecto', timeline: 'Cronología del progreso', message: 'Mensaje',
    attach: 'Agregar fotos privadas del progreso', submit: 'Guardar entrada', photo: 'Foto privada adjunta a esta entrada',
    history: 'Historial de servicios', reload: 'Recargar registro actual',
    denied: 'Este registro no está disponible o tu cuenta ya no tiene acceso.',
  },
}
const result = { status: 'FAIL', checks: [], geometry: [], writes: 0, unexpected_mutations: 0,
  external_navigation: false, blocked_external_requests: 0 }
const contexts = new Set()
let fixture, propertyId, projectId, milestoneId, updateId, photoId, digest, activeCheck = 'initialize'
class CheckError extends Error {}
function check(value, code) { if (!value) throw new CheckError(code) }
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const text = (scope, value) => scope.getByText(value, { exact: true })
const panel = (page, name) => page.locator('section').filter({ has: page.getByRole('heading', { name, exact: true, level: 2 }) })
const projectPath = () => `${network}/projects/${projectId}`
const registryPath = () => `${network}/registry/${propertyId}`
const mediaPath = () => `${network}/media/${photoId}`
const commentsPath = () => `${network}/updates/${updateId}/comments`
const noStore = response => /\bno-store\b/.test(response.headers()['cache-control'] ?? '')
const endpoint = (response, path, method = 'GET') => {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function field(scope, prompt, selector) {
  const input = scope.locator('label').filter({ hasText: prompt }).locator(selector)
  await input.first().waitFor()
  check(await input.count() === 1 && await input.evaluate((element, expected) => {
    const label = element.closest('label')
    const value = [...(label?.childNodes ?? [])].filter(node => node.nodeType === Node.TEXT_NODE)
      .map(node => node.textContent).join('').replace(/\s+/g, ' ').trim()
    return label?.control === element && value === expected
  }, prompt), 'NATIVE_LABEL_ASSOCIATION_REQUIRED')
  return input
}

async function readFixture() {
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'FIXTURE_PATH_INVALID')
  const info = await lstat(path)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 8192, 'PRIVATE_FIXTURE_FILE_REQUIRED')
  const value = JSON.parse(await readFile(path, 'utf8'))
  check(exact(value, ['accounts', 'codes', 'facts']) && exact(value.accounts, Object.keys(roles))
    && exact(value.codes, Object.keys(roles)), 'FIXTURE_SHAPE_INVALID')
  for (const actor of Object.keys(roles)) {
    check(exact(value.accounts[actor], ['email', 'password'])
      && value.accounts[actor].email === `browser-owner-project-${actor}@example.com`
      && value.accounts[actor].password.length === 32 && Number.isSafeInteger(value.codes[actor])
      && value.codes[actor] > 0, 'FIXTURE_ACCOUNT_INVALID')
  }
  check(exact(value.facts, ['property', 'location', 'address', 'notes', 'project', 'summary', 'milestone', 'update', 'comment', 'category'])
    && Object.values(value.facts).every(item => typeof item === 'string' && item.length > 0 && item.length < 200), 'FIXTURE_FACTS_INVALID')
  return value
}

async function session(browser, actor, lang, width) {
  const context = await browser.newContext({ viewport: { width, height: 844 }, serviceWorkers: 'block',
    locale: lang === 'es' ? 'es-DO' : 'en-US' })
  contexts.add(context)
  context.setDefaultTimeout(wait); context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ origin, lang }) => {
    if (location.origin !== origin) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { origin: dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method()
    if (![dashboard, api].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) result.external_navigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const allowed = actor === 'owner' ? [
        ['POST', `${network}/registry`], ['POST', `${network}/projects`],
        ['POST', `${projectPath()}/members`], ['POST', `${projectPath()}/milestones`],
        ['PUT', `${network}/milestones/${milestoneId}`], ['POST', commentsPath()],
        ['POST', `${projectPath()}/members/${fixture.codes.professional}/revoke`],
      ] : actor === 'professional' ? [['POST', `${network}/media`], ['POST', `${projectPath()}/updates`]] : []
      if (url.origin !== api || url.search || !allowed.some(([verb, path]) => method === verb && path === url.pathname)) {
        result.unexpected_mutations += 1
        return route.abort('blockedbyclient')
      }
      result.writes += 1
    }
    return route.continue()
  })
  const account = actor ? fixture.accounts[actor] : null
  if (actor) {
    const response = await context.request.post(`${api}/auth/login`, { data: account,
      headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait })
    try {
      const cookie = (await context.cookies(api)).find(value => value.name === 'ildr_token')
      check(response.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'GENUINE_COOKIE_LOGIN_REQUIRED')
    } finally { await response.dispose() }
  }
  const page = await context.newPage()
  page.on('pageerror', () => { result.page_errors = (result.page_errors ?? 0) + 1 })
  return { context, page, actor, account, lang, width, t: copy[lang] }
}
async function foreground(state) {
  await state.page.bringToFront()
  await state.page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}
async function visit(state, path) {
  await state.page.bringToFront()
  const [response] = await Promise.all([state.page.waitForResponse(value => endpoint(value, '/auth/me')),
    state.page.goto(dashboard + path, { waitUntil: 'domcontentloaded' })])
  const user = await response.json()
  check(response.status() === 200 && user.email === state.account.email && user.role === roles[state.actor], 'ACTOR_ROLE_MISMATCH')
  await foreground(state)
  check(await state.page.evaluate(lang => localStorage.getItem('ildr_lang') === lang && !document.cookie.includes('ildr_token='), state.lang), 'PRIVATE_SESSION_REQUIRED')
}
async function read(state, path, status = 200, image = false) {
  const response = await state.context.request.get(api + path, { maxRedirects: 0, timeout: wait })
  try {
    check(response.status() === status && (path === '/professionals/options' || noStore(response)), 'PRIVATE_READ_STATUS_OR_CACHE')
    if (image) {
      check(response.headers()['content-type'] === 'image/webp' && response.headers()['content-disposition'] === 'inline', 'PRIVATE_MEDIA_HEADERS')
      return createHash('sha256').update(await response.body()).digest('hex')
    }
    const data = await response.json()
    if (status >= 400) check(exact(data, ['detail']), 'DENIAL_PROJECTION_INVALID')
    return data
  } finally { await response.dispose() }
}
async function write(page, path, method, status, action) {
  const [response] = await Promise.all([page.waitForResponse(value => endpoint(value, path, method)), action()])
  check(response.status() === status && noStore(response), 'REAL_UI_WRITE_STATUS_OR_CACHE')
  return response.json()
}
async function geometry(state, name) {
  const dimensions = await state.page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth }))
  check(dimensions.width === state.width && dimensions.document <= state.width && dimensions.body <= state.width, 'WHOLE_PAGE_MOBILE_OVERFLOW')
  result.geometry.push({ name, lang: state.lang, ...dimensions })
}
async function tab(state, name) {
  await foreground(state)
  await button(state.page.getByRole('navigation', { name: state.t.tabs, exact: true }), name).click()
}
async function attachment(state, scope) {
  const image = scope.getByRole('img', { name: state.t.photo, exact: true }).first()
  await image.waitFor(); await image.evaluate(node => node.decode())
  check(await image.evaluate(node => node.src.startsWith('blob:') && node.naturalWidth === 36 && node.naturalHeight === 24), 'REAL_SANITIZED_PHOTO_RENDER_REQUIRED')
}
async function run(name, action) { activeCheck = name; await action(); result.checks.push(name) }

async function main() {
  fixture = await readFixture()
  const facts = fixture.facts
  const browser = await chromium.launch({ headless: true, args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  try {
    const owner = await session(browser, 'owner', 'en', 390)
    const professional = await session(browser, 'professional', 'es', 320)
    const outsider = await session(browser, 'outsider', 'en', 390)
    await run('owner_registers_authorized_private_property_from_normal_form', async () => {
      await visit(owner, `${network}?tab=properties`)
      await button(owner.page, owner.t.add).click()
      const form = panel(owner.page, owner.t.add)
      for (const [label, value, type] of [[owner.t.name, facts.property, 'input'], [owner.t.location, facts.location, 'input'],
        [owner.t.address, facts.address, 'textarea'], [owner.t.notes, facts.notes, 'textarea']]) {
        await (await field(form, label, type)).fill(value)
      }
      await (await field(form, owner.t.class, 'select')).selectOption('house_villa')
      await (await field(form, owner.t.relationship, 'select')).selectOption('owner')
      check(await button(form, owner.t.register).isDisabled(), 'AUTHORITY_ACKNOWLEDGEMENT_REQUIRED')
      await form.getByRole('checkbox', { name: owner.t.authority, exact: true }).check()
      await geometry(owner, 'private_property_form')
      const saved = await write(owner.page, `${network}/registry`, 'POST', 201, () => button(form, owner.t.register).click())
      check(uuid.test(saved.id) && saved.title === facts.property && saved.address === facts.address
        && saved.notes === facts.notes && saved.can_create_project, 'PRIVATE_PROPERTY_RESULT_INVALID')
      propertyId = saved.id
      await button(owner.page, owner.t.createFor).waitFor()
      check(new URL(owner.page.url()).searchParams.get('property') === propertyId, 'CREATED_PROPERTY_NORMAL_NAVIGATION_REQUIRED')
      const catalog = await read(owner, '/professionals/options')
      const category = catalog.categories.find(value => value.id === facts.category)
      check(category?.label?.en && category?.label?.es, 'ACTUAL_SERVICE_CATALOG_REQUIRED')
      fixture.categoryLabel = category.label.en
    })
    await run('owner_creates_linked_project_and_assigns_professional', async () => {
      await button(owner.page, owner.t.createFor).click()
      const form = panel(owner.page, owner.t.create)
      await (await field(form, owner.t.title, 'input')).fill(facts.project)
      await (await field(form, owner.t.summary, 'textarea')).fill(facts.summary)
      const propertyClass = await field(form, owner.t.class, 'select')
      check(await propertyClass.isDisabled() && await propertyClass.inputValue() === 'house_villa', 'SOURCE_CLASS_LINK_REQUIRED')
      await form.getByRole('checkbox', { name: fixture.categoryLabel, exact: true }).check()
      await geometry(owner, 'private_project_form')
      const project = await write(owner.page, `${network}/projects`, 'POST', 201, () => button(form, owner.t.create).click())
      check(uuid.test(project.id) && project.property_id === propertyId && project.listing_id === null && project.my_role === 'owner', 'PROJECT_SOURCE_LINK_INVALID')
      projectId = project.id
      await owner.page.getByRole('heading', { name: facts.project, exact: true, level: 1 }).waitFor()
      await tab(owner, owner.t.members)
      const team = panel(owner.page, owner.t.members)
      await (await field(team, owner.t.code, 'input[type="number"]')).fill(String(fixture.codes.professional))
      await (await field(team, owner.t.role, 'select')).selectOption('professional')
      const member = await write(owner.page, `${projectPath()}/members`, 'POST', 200, () => button(team, owner.t.grant).click())
      check(Array.isArray(member) && member.some(value => value.user_code === fixture.codes.professional
        && value.role === 'professional' && value.active), 'ASSIGNMENT_RESULT_INVALID')
      await button(team, owner.t.revoke).waitFor()
    })
    await run('owner_records_milestone_and_live_completion_progress', async () => {
      await tab(owner, owner.t.milestones)
      const milestones = panel(owner.page, owner.t.milestonePanel)
      const create = milestones.locator('form').filter({ has: button(milestones, owner.t.addMilestone) })
      await (await field(create, owner.t.milestone, 'input')).fill(facts.milestone)
      const saved = await write(owner.page, `${projectPath()}/milestones`, 'POST', 201, () => button(create, owner.t.addMilestone).click())
      check(uuid.test(saved.id) && saved.status === 'open', 'MILESTONE_CREATE_INVALID')
      milestoneId = saved.id
      const edit = milestones.locator('form').filter({ has: button(milestones, owner.t.save) })
      await (await field(edit, owner.t.status, 'select')).selectOption('completed')
      const done = await write(owner.page, `${network}/milestones/${milestoneId}`, 'PUT', 200, () => button(edit, owner.t.save).click())
      check(done.status === 'completed' && done.id === milestoneId, 'MILESTONE_COMPLETION_INVALID')
      await text(owner.page, '1 of 1 milestones completed · 100%').waitFor()
      const project = await read(owner, projectPath())
      check(project.progress.completed === 1 && project.progress.total === 1 && project.progress.percent === 100, 'LIVE_PROGRESS_PROJECTION_INVALID')
      await geometry(owner, 'completed_milestone')
    })
    await run('assigned_professional_posts_dated_private_progress_photo_in_spanish', async () => {
      await visit(professional, `${network}?tab=projects&project=${projectId}`)
      await professional.page.getByRole('heading', { name: facts.project, exact: true, level: 1 }).waitFor()
      const timeline = panel(professional.page, professional.t.timeline)
      const form = timeline.locator('form').filter({ has: timeline.locator('input[type="file"]') })
      const png = await professional.page.evaluate(() => {
        const canvas = document.createElement('canvas'); canvas.width = 36; canvas.height = 24
        const context = canvas.getContext('2d'); context.fillStyle = '#1c8075'; context.fillRect(0, 0, 36, 24)
        return canvas.toDataURL('image/png').split(',')[1]
      })
      const uploaded = await write(professional.page, `${network}/media`, 'POST', 201, async () =>
        (await field(form, professional.t.attach, 'input[type="file"]')).setInputFiles({ name: 'synthetic-progress.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') }))
      check(uuid.test(uploaded.id) && uploaded.content_type === 'image/webp' && uploaded.width === 36 && uploaded.height === 24
        && /^[0-9a-f]{64}$/.test(uploaded.sha256), 'SANITIZED_UPLOAD_INVALID')
      photoId = uploaded.id; digest = uploaded.sha256
      await (await field(form, professional.t.message, 'textarea')).fill(facts.update)
      await attachment(professional, form)
      await geometry(professional, 'professional_progress_composer')
      const update = await write(professional.page, `${projectPath()}/updates`, 'POST', 201, () => button(form, professional.t.submit).click())
      check(uuid.test(update.id) && update.body === facts.update && update.media_ids.length === 1 && update.media_ids[0] === photoId, 'REAL_PROGRESS_PHOTO_LINK_INVALID')
      updateId = update.id
      await text(timeline, facts.update).waitFor()
      check(await read(professional, mediaPath(), 200, true) === digest, 'PROFESSIONAL_PHOTO_BYTES_INVALID')
      await read(professional, registryPath(), 404)
      check(!JSON.stringify(await read(professional, projectPath())).includes(facts.address), 'PRIVATE_DOSSIER_LEAKED_TO_TEAM')
    })
    await run('owner_comments_on_exact_photo_and_reads_property_history', async () => {
      await tab(owner, owner.t.timeline)
      const timeline = panel(owner.page, owner.t.timeline)
      const entry = timeline.locator('li').filter({ has: text(timeline, facts.update) })
      await text(entry, facts.update).waitFor(); await attachment(owner, entry)
      await button(entry, owner.t.comments).click()
      await (await field(entry, owner.t.context, 'select')).selectOption(photoId)
      await (await field(entry, owner.t.message, 'textarea')).fill(facts.comment)
      const comment = await write(owner.page, commentsPath(), 'POST', 201, () => button(entry, owner.t.submit).click())
      check(comment.media_id === photoId && comment.body === facts.comment, 'PHOTO_SPECIFIC_COMMENT_INVALID')
      await text(entry, facts.comment).waitFor()
      await tab(owner, owner.t.history)
      await text(panel(owner.page, owner.t.history), facts.update).waitFor()
      await attachment(owner, panel(owner.page, owner.t.history))
      const history = await read(owner, `${registryPath()}/history`)
      check(history.items.some(item => item.id === updateId && item.project_id === projectId && item.media_ids.includes(photoId)), 'PROPERTY_HISTORY_LINK_INVALID')
      await geometry(owner, 'owner_property_history')
    })
    await run('foreign_and_anonymous_scope_cannot_read_project_history_or_photo', async () => {
      await visit(outsider, `${network}?tab=projects&project=${projectId}`)
      await text(outsider.page, outsider.t.denied).waitFor()
      check(await text(outsider.page, facts.update).count() === 0, 'FOREIGN_PROJECT_DOM_LEAK')
      const guest = await session(browser, null, 'en', 390)
      for (const path of [registryPath(), projectPath(), `${projectPath()}/updates`, `${registryPath()}/history`, commentsPath(), mediaPath()]) {
        await read(outsider, path, 404); await read(guest, path, 401)
      }
    })
    await run('owner_revocation_clears_open_professional_view_and_retains_owner_history', async () => {
      await tab(owner, owner.t.members)
      const team = panel(owner.page, owner.t.members)
      const denied = professional.page.waitForResponse(value => endpoint(value, projectPath()) && value.status() === 404)
      await write(owner.page, `${projectPath()}/members/${fixture.codes.professional}/revoke`, 'POST', 200, () => button(team, owner.t.revoke).click())
      await foreground(professional)
      await professional.page.evaluate(() => window.dispatchEvent(new Event('focus')))
      check(noStore(await denied), 'REVOCATION_DENIAL_CACHE')
      await text(professional.page, professional.t.denied).waitFor()
      check(await text(professional.page, facts.update).count() === 0
        && await professional.page.getByRole('img', { name: professional.t.photo, exact: true }).count() === 0,
      'REVOKED_PRIVATE_DOM_RETAINED')
      for (const path of [projectPath(), `${projectPath()}/updates`, `${registryPath()}/history`, commentsPath(), mediaPath()]) await read(professional, path, 404)
      const retained = await read(owner, commentsPath())
      check(retained.items.length === 1 && retained.items[0].media_id === photoId && retained.items[0].body === facts.comment
        && await read(owner, mediaPath(), 200, true) === digest, 'OWNER_HISTORY_OR_PHOTO_LOST')
      await geometry(professional, 'revoked_professional_view')
    })
    check(result.writes === 9 && !result.unexpected_mutations && !result.external_navigation
      && !result.page_errors && result.geometry.length === 6, 'BROWSER_CONTAINMENT_OR_GEOMETRY_INVALID')
  } finally {
    await Promise.all([...contexts].map(context => context.close()))
    await browser.close()
  }
}
try { await main(); result.status = 'PASS' }
catch (error) {
  result.failed_check = activeCheck
  result.error = error instanceof CheckError ? error.message : error?.name === 'TimeoutError' ? 'TIMEOUT' : 'UNEXPECTED'
  const line = String(error?.stack ?? '').match(/run-owner-project-browser\.mjs:(\d+):\d+/)?.[1]
  if (line) result.line = Number(line)
  process.exitCode = 1
}
console.log('OWNER_PROJECT_BROWSER_RESULT ' + JSON.stringify(result))
