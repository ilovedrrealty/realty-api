import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const slug = 'synthetic-editorial-neighborhood'
const privateNote = 'PRIVATE_SYNTHETIC_EDITORIAL_NOTE'
const title = 'Synthetic neighborhood article'
const spanishTitle = 'Artículo sintético del barrio'
const updatedTitle = 'Synthetic draft edit'
const checks = []
let step = 'fixture'

class CheckFailure extends Error {}
function check(condition, reason) {
  if (!condition) throw new CheckFailure(reason)
}

async function readFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const info = await lstat(fixturePath)
  check(info.isFile() && (info.mode & 0o777) === 0o600 && info.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const data = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(['admin', 'realtor'].every(role => data.accounts?.[role]?.email === `browser-${role}@example.com`
    && /^[a-zA-Z0-9_-]{32}$/.test(data.accounts[role].password)), 'EXACT_SYNTHETIC_ACCOUNTS_REQUIRED')
  return data
}

function fromApi(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

function noCache(response) {
  check((response.headers()['cache-control'] ?? '').includes('no-store'), 'NO_STORE_EDITORIAL_RESPONSE_REQUIRED')
}

function call(context, path, method = 'GET', data = undefined) {
  return context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
}

async function apiResult(context, path, expected = [200], method = 'GET', body = undefined) {
  const result = await call(context, path, method, body)
  check(expected.includes(result.status()), 'EDITORIAL_API_STATE_MISMATCH')
  noCache(result)
  const data = await result.json()
  await result.dispose()
  return data
}

async function session(browser, account = null) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block' })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return [dashboard, api].includes(target.origin) ? route.continue() : route.abort('blockedbyclient')
  })
  await context.addInitScript(() => {
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    const response = await call(context, '/auth/login', 'POST', account)
    check(response.status() === 200 && (await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_SESSION_REQUIRED')
    await response.dispose()
  }
  return context
}

async function act(page, path, control, confirmation) {
  await page.getByRole('button', { name: control, exact: true }).click()
  const response = page.waitForResponse(item => fromApi(item, path, 'POST'))
  await page.getByRole('button', { name: confirmation, exact: true }).click()
  const result = await response
  check(result.status() === 200, 'CONFIRMED_EDITORIAL_ACTION_REQUIRED')
  noCache(result)
  return result.json()
}

function nameInput(page, name) {
  return page.getByRole('textbox', { name: new RegExp('^' + name) })
}

async function main() {
  const fixture = await readFixture()
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'editorial_auth'
    const admin = await session(browser, fixture.accounts.admin)
    const realtor = await session(browser, fixture.accounts.realtor)
    const publicUser = await session(browser)
    const published = '/site/editorial/' + slug
    await apiResult(publicUser, '/admin/editorial', [401])
    await apiResult(realtor, '/admin/editorial', [403])
    await apiResult(publicUser, published + '?locale=en', [404])
    const page = await admin.newPage()
    await page.bringToFront()
    const identityPromise = page.waitForResponse(result => fromApi(result, '/auth/me'))
    await page.goto(dashboard + '/editorial?workspace=admin', { waitUntil: 'domcontentloaded' })
    const identity = await identityPromise
    check(identity.ok() && (await identity.json()).role === 'admin', 'EXACT_ADMIN_SESSION_REQUIRED')
    checks.push('anonymous_and_realtor_cannot_read_admin_editorial')

    step = 'create_partial_draft'
    await page.getByRole('button', { name: 'New article or guide', exact: true }).click()
    await nameInput(page, 'Slug').fill(slug)
    await page.getByRole('combobox', { name: /^Content type/ }).selectOption('neighborhood')
    const en = page.getByRole('group', { name: 'English', exact: true })
    await nameInput(en, 'Title').fill(title)
    await nameInput(en, 'Summary').fill('Synthetic English summary.')
    await nameInput(en, 'Body').fill('Synthetic English body.')
    await nameInput(page, 'Review note').fill(privateNote)
    const creating = page.waitForResponse(result => fromApi(result, '/admin/editorial', 'POST'))
    await page.getByRole('button', { name: 'Save draft', exact: true }).click()
    const created = await creating
    check(created.status() === 201, 'INCOMPLETE_DRAFT_SAVE_REQUIRED')
    noCache(created)
    const draft = await created.json()
    check(draft.draft_revision === 1 && draft.published_revision === null && draft.content.es.body === '',
      'INITIAL_DRAFT_SCOPE_REQUIRED')
    const owned = '/admin/editorial/' + draft.id
    await apiResult(admin, owned + '/review', [409, 422], 'POST', { expected_version: draft.version, revision: draft.draft_revision })
    await apiResult(publicUser, published + '?locale=es', [404])

    step = 'save_bilingual_draft'
    const es = page.getByRole('group', { name: 'Spanish', exact: true })
    await nameInput(es, 'Title').fill(spanishTitle)
    await nameInput(es, 'Summary').fill('Resumen de ejemplo.')
    await nameInput(es, 'Body').fill('Texto revisado de ejemplo.')
    const add = page.getByRole('button', { name: 'Add source', exact: true })
    const firstSource = nameInput(page, 'Source label 1')
    if (!(await firstSource.isVisible())) await add.click()
    await firstSource.fill('Synthetic source')
    await nameInput(page, 'Source URL 1').fill('https://source.example.test/details')
    const saving = page.waitForResponse(result => fromApi(result, owned + '/draft', 'PUT'))
    await page.getByRole('button', { name: 'Save draft', exact: true }).click()
    const saved = await saving
    check(saved.status() === 200, 'COMPLETE_DRAFT_SAVE_REQUIRED')
    noCache(saved)
    const complete = await saved.json()
    check(complete.content.es.title === spanishTitle && complete.draft_revision > draft.draft_revision
      && complete.published_revision === null && complete.review_note === privateNote, 'BILINGUAL_VERSION_AND_PRIVATE_NOTE_REQUIRED')
    checks.push('incomplete_draft_is_editable_but_cannot_be_reviewed_or_read_publicly')

    step = 'private_spanish_preview'
    await page.getByRole('button', { name: 'Preview draft', exact: true }).click()
    await page.getByRole('combobox', { name: /^Preview language/ }).selectOption('es')
    await page.getByText(spanishTitle, { exact: true }).waitFor({ state: 'visible' })
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'EDITORIAL_PREVIEW_MOBILE_OVERFLOW')
    await page.getByRole('button', { name: 'Close preview', exact: true }).click()
    await apiResult(publicUser, published + '?locale=es', [404])
    checks.push('private_spanish_preview_at_mobile_width')

    step = 'review_and_publish'
    const reviewed = await act(page, owned + '/review', 'Review revision', 'Confirm review')
    check(reviewed.reviewed_revision === complete.draft_revision && reviewed.published_revision === null,
      'REVIEW_MUST_NOT_PUBLISH')
    await apiResult(publicUser, published + '?locale=en', [404])
    const publication = await act(page, owned + '/publish', 'Publish revision', 'Confirm publish')
    check(publication.published_revision === complete.draft_revision, 'EXACT_REVIEWED_REVISION_REQUIRED')
    const readers = await Promise.all([
      apiResult(publicUser, published + '?locale=en'), apiResult(publicUser, published + '?locale=es'),
    ])
    check(readers[0].title === title && readers[1].title === spanishTitle
      && readers.every(item => item.sources.length === 1 && !JSON.stringify(item).includes(privateNote)), 'BILINGUAL_PUBLIC_PROJECTION_REQUIRED')
    checks.push('reviewed_version_appears_in_both_languages_without_private_metadata')

    step = 'draft_edit_does_not_replace_publication'
    await nameInput(en, 'Title').fill(updatedTitle)
    const unsent = page.waitForResponse(result => fromApi(result, owned + '/draft', 'PUT'))
    await page.getByRole('button', { name: 'Save draft', exact: true }).click()
    const changed = await unsent
    check(changed.status() === 200, 'PENDING_DRAFT_EDIT_REQUIRED')
    noCache(changed)
    const update = await changed.json()
    check(update.reviewed_revision === null && update.published_revision === complete.draft_revision,
      'NEW_DRAFT_REQUIRES_NEW_REVIEW')
    const stillPublic = await apiResult(publicUser, published + '?locale=en')
    check(stillPublic.title === title, 'PUBLISHED_REVISION_MUST_STAY_STABLE')
    const historic = await apiResult(admin, owned + '/revisions')
    check(historic.items.some(item => item.revision === complete.draft_revision)
      && historic.items.some(item => item.revision === update.draft_revision), 'REVISION_HISTORY_REQUIRED')
    await apiResult(admin, owned + '/unpublish', [409], 'POST', { expected_version: complete.version })
    checks.push('unreviewed_edits_leave_existing_publication_and_stale_change_conflicts')

    step = 'confirmed_unpublish'
    const removed = await act(page, owned + '/unpublish', 'Unpublish', 'Confirm unpublish')
    check(removed.published_revision === null, 'EXPLICIT_UNPUBLISH_REQUIRED')
    await apiResult(publicUser, published + '?locale=en', [404])
    await apiResult(publicUser, published + '?locale=es', [404])
    const restored = await apiResult(admin, owned + '/restore', [200], 'POST',
      { expected_version: removed.version, revision: complete.draft_revision })
    check(restored.draft_revision > update.draft_revision && restored.reviewed_revision === null
      && restored.published_revision === null && restored.content.en.title === title, 'RESTORE_MUST_CREATE_PRIVATE_DRAFT')
    await apiResult(publicUser, published + '?locale=en', [404])
    checks.push('withdrawal_hides_both_languages_and_restore_creates_private_draft')
    console.log('EDITORIAL_ADMIN_BROWSER ' + JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

try {
  await main()
} catch (error) {
  const reason = error instanceof CheckFailure ? error.message
    : error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
  console.log('EDITORIAL_ADMIN_BROWSER ' + JSON.stringify({ status: 'FAIL', step, reason, checks }))
  process.exitCode = 1
}
