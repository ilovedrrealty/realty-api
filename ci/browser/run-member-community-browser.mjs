import { createHash } from 'node:crypto'
import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const network = '/property-network'
const wait = 12_000
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const roles = { owner: 'owner', author: 'buyer', reader: 'buyer' }
const copy = {
  es: {
    private: 'Perfil privado de la red', visible: 'Visible para miembros con sesión iniciada',
    edit: 'Editar mi perfil en la red', name: 'Nombre visible del miembro', status: 'Estado actual / en qué estoy trabajando',
    visibility: '¿Quién puede ver mi perfil de la red?', policy: '¿Quién puede publicar en mi muro?',
    save: 'Guardar cambios', wall: 'Muro del miembro', own: 'Publicar una actualización en mi muro',
    message: 'Mensaje', submit: 'Guardar entrada', pending: 'Pendiente de aprobación del propietario del muro',
    approve: 'Aprobar publicación', visiblePost: 'Visible para la audiencia permitida', refresh: 'Actualizar',
    privacy: 'Controles de privacidad entre miembros', code: 'Código de miembro', block: 'Bloquear miembro',
  },
  en: {
    wall: 'Member wall', message: 'Message', submit: 'Save entry', attach: 'Add private progress photos',
    photoAlt: 'Private photo attached to this entry', refresh: 'Refresh', comments: 'View comments',
    commentContext: 'This comment is about', photo: 'Photo 1',
    denied: 'This record is unavailable or your current account no longer has access.',
  },
}
const result = { status: 'FAIL', checks: [], writes: 0, unexpected_mutations: 0, blocked_external_requests: 0,
  external_navigation: false }
const contexts = new Set()
let fixture, photoId, crossPostId, activeCheck = 'initialize'

class CheckError extends Error {}
function check(ok, code) { if (!ok) throw new CheckError(code) }

const exact = (object, keys) => object && typeof object === 'object' && !Array.isArray(object)
  && Object.keys(object).length === keys.length && keys.every(key => Object.hasOwn(object, key))
const exactText = (scope, text) => scope.getByText(text, { exact: true })
const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const panel = (page, name) => page.locator('section').filter({
  has: page.getByRole('heading', { name, exact: true, level: 2 }),
})
const profilePath = code => `${network}/profiles/${code}`
const wallPath = code => `${profilePath(code)}/posts`
const commentsPath = id => `${network}/posts/${id}/comments`
const mediaPath = id => `${network}/media/${id}`
const approvePath = id => `${network}/posts/${id}/moderate`
const blockPath = code => `${network}/me/blocks/${code}`
const wallURL = code => `${network}?tab=people&member=${code}`
const noStore = response => /\bno-store\b/.test(response.headers()['cache-control'] ?? '')

async function readFixture() {
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path === '/browser-fixtures/fixtures.json', 'FIXTURE_PATH_INVALID')
  const facts = await lstat(path)
  check(facts.isFile() && (facts.mode & 0o777) === 0o600 && facts.size < 4096, 'PRIVATE_FIXTURE_FILE_REQUIRED')
  const data = JSON.parse(await readFile(path, 'utf8'))
  check(exact(data, ['accounts', 'codes', 'profile', 'status', 'ownPost', 'crossPost', 'comment'])
    && exact(data.accounts, Object.keys(roles)) && exact(data.codes, Object.keys(roles)), 'FIXTURE_SHAPE_INVALID')
  for (const actor of Object.keys(roles)) {
    const account = data.accounts[actor]
    check(exact(account, ['email', 'password']) && account.email === `browser-wall-${actor}@example.com`
      && typeof account.password === 'string' && account.password.length === 32
      && Number.isSafeInteger(data.codes[actor]) && data.codes[actor] > 0, 'FIXTURE_ACCOUNT_INVALID')
  }
  check(new Set(Object.values(data.codes)).size === 3 && ['profile', 'status', 'ownPost', 'crossPost', 'comment']
    .every(key => typeof data[key] === 'string' && data[key].length > 0 && data[key].length < 100), 'FIXTURE_DATA_INVALID')
  return data
}

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function session(browser, actor, lang) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block',
    locale: lang === 'es' ? 'es-DO' : 'en-US' })
  contexts.add(context)
  context.setDefaultTimeout(wait)
  context.setDefaultNavigationTimeout(wait)
  await context.addInitScript(({ dashboard, lang }) => {
    if (location.origin !== dashboard) return
    localStorage.setItem('ildr_lang', lang)
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  }, { dashboard, lang })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method()
    if (![dashboard, api].includes(url.origin)) {
      result.blocked_external_requests += 1
      if (request.isNavigationRequest()) result.external_navigation = true
      return route.abort('blockedbyclient')
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const allowed = {
        owner: [['PUT', `${network}/me/profile`], ['POST', wallPath(fixture.codes.owner)],
          ['POST', approvePath(crossPostId)], ['PUT', blockPath(fixture.codes.reader)]],
        author: [['POST', `${network}/media`], ['POST', wallPath(fixture.codes.owner)]],
        reader: [['POST', commentsPath(crossPostId)]],
      }[actor] ?? []
      if (url.origin !== api || url.search || !allowed.some(([verb, path]) => verb === method && path === url.pathname)) {
        result.unexpected_mutations += 1
        return route.abort('blockedbyclient')
      }
      result.writes += 1
    }
    return route.continue()
  })
  if (!actor) return { context }
  const account = fixture.accounts[actor]
  const response = await context.request.post(`${api}/auth/login`, {
    data: account, headers: { Origin: dashboard }, maxRedirects: 0, timeout: wait,
  })
  try {
    const cookie = (await context.cookies(api)).find(item => item.name === 'ildr_token')
    check(response.status() === 200 && cookie?.httpOnly && cookie.domain === '127.0.0.1', 'SYNTHETIC_AUTH_FAILED')
  } finally { await response.dispose() }
  return { context, page: await context.newPage(), actor, account, lang, t: copy[lang] }
}

async function foreground(page) {
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible' && navigator.onLine)
}

async function visit(state, path) {
  await state.page.bringToFront()
  const [auth] = await Promise.all([
    state.page.waitForResponse(response => endpoint(response, '/auth/me')),
    state.page.goto(`${dashboard}${path}`, { waitUntil: 'domcontentloaded' }),
  ])
  const identity = await auth.json()
  check(auth.status() === 200 && identity.role === roles[state.actor] && identity.email === state.account.email,
    'AUTH_ACTOR_MISMATCH')
  await foreground(state.page)
  check(await state.page.evaluate(lang => localStorage.getItem('ildr_lang') === lang && innerWidth === 390
    && !document.cookie.includes('ildr_token='), state.lang), 'MOBILE_CONTEXT_MISMATCH')
}

async function read(state, path, status = 200, bytes = false) {
  const response = await state.context.request.get(`${api}${path}`, { maxRedirects: 0, timeout: wait })
  try {
    check(response.status() === status && noStore(response), 'DIRECT_READ_STATUS_OR_HEADERS')
    if (bytes) {
      check(response.headers()['content-type'] === 'image/webp'
        && response.headers()['content-disposition'] === 'inline', 'PRIVATE_PHOTO_HEADERS_MISMATCH')
      return createHash('sha256').update(await response.body()).digest('hex')
    }
    const value = await response.json()
    if (status >= 400) check(exact(value, ['detail']), 'PRIVATE_DENIAL_PROJECTION_MISMATCH')
    return value
  } finally { await response.dispose() }
}

async function write(page, path, method, status, act) {
  const [response] = await Promise.all([page.waitForResponse(value => endpoint(value, path, method)), act()])
  check(response.status() === status && noStore(response), 'UI_WRITE_STATUS_OR_HEADERS')
  return response.json()
}

async function entry(state, scope, text) {
  await scope.getByLabel(state.t.message, { exact: true }).fill(text)
  const id = scope.getByLabel(state.t.message, { exact: true })
  check(await id.inputValue() === text, 'DRAFT_MESSAGE_MISMATCH')
}

function postArticle(wall, body) { return wall.locator('article').filter({ hasText: body }) }

async function attachment(state, article) {
  const image = article.getByRole('img', { name: state.t.photoAlt, exact: true }).first()
  await image.waitFor()
  await image.evaluate(node => node.decode())
  check(await image.evaluate(node => node.src.startsWith('blob:') && node.naturalWidth === 36 && node.naturalHeight === 24),
    'ACTUAL_PRIVATE_PHOTO_NOT_RENDERED')
}

async function run(name, task) {
  activeCheck = name
  await task()
  result.checks.push(name)
}

async function main() {
  fixture = await readFixture()
  const browser = await chromium.launch({ headless: true,
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'] })
  try {
    const owner = await session(browser, 'owner', 'es')
    const ownerCode = fixture.codes.owner
    const ownerWall = panel(owner.page, owner.t.wall)
    let reader, author, readerWall, digest

    await run('private_default_then_member_opt_in_es', async () => {
      await visit(owner, `${network}?tab=my-profile`)
      await exactText(owner.page, owner.t.private).waitFor()
      const initial = await read(owner, `${network}/me/profile`)
      check(initial.version === 0 && initial.visibility === 'private' && initial.wall_policy === 'own_only'
        && initial.user_code === ownerCode, 'PRIVATE_OWNER_DEFAULT_MISMATCH')
      reader = await session(browser, 'reader', 'en')
      await read(reader, profilePath(ownerCode), 404)
      await foreground(owner.page)
      const form = panel(owner.page, owner.t.edit)
      await form.getByLabel(owner.t.name, { exact: true }).fill(fixture.profile)
      await form.getByLabel(owner.t.status, { exact: true }).fill(fixture.status)
      await form.getByLabel(owner.t.visibility, { exact: true }).selectOption('members')
      await form.getByLabel(owner.t.policy, { exact: true }).selectOption('approval_required')
      const saved = await write(owner.page, `${network}/me/profile`, 'PUT', 200, () => button(form, owner.t.save).click())
      check(saved.version === 1 && saved.visibility === 'members' && saved.wall_policy === 'approval_required'
        && saved.status_text === fixture.status, 'MEMBER_OPT_IN_MISMATCH')
      await exactText(owner.page, owner.t.visible).waitFor()
      await owner.page.getByRole('heading', { name: fixture.profile, exact: true }).waitFor()
      await exactText(ownerWall, owner.t.own).waitFor()
    })

    await run('owner_posts_own_wall_es', async () => {
      await entry(owner, ownerWall, fixture.ownPost)
      const posted = await write(owner.page, wallPath(ownerCode), 'POST', 201, () => button(ownerWall, owner.t.submit).click())
      check(posted.status === 'visible' && posted.body === fixture.ownPost && posted.media_ids.length === 0,
        'OWN_WALL_POST_MISMATCH')
      await exactText(ownerWall, fixture.ownPost).waitFor()
    })

    await run('foreign_wall_photo_waits_for_approval_en', async () => {
      author = await session(browser, 'author', 'en')
      await visit(author, wallURL(ownerCode))
      const authorWall = panel(author.page, author.t.wall)
      await exactText(author.page, fixture.status).waitFor()
      await exactText(authorWall, fixture.ownPost).waitFor()
      const png = await author.page.evaluate(() => {
        const image = document.createElement('canvas')
        image.width = 36; image.height = 24
        const context = image.getContext('2d')
        context.fillStyle = '#c05a20'; context.fillRect(0, 0, 36, 24)
        return image.toDataURL('image/png').split(',')[1]
      })
      const uploaded = await write(author.page, `${network}/media`, 'POST', 201, () =>
        authorWall.getByLabel(author.t.attach, { exact: true }).setInputFiles({
          name: 'synthetic-wall-photo.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64'),
        }))
      check(uuid.test(uploaded.id) && uploaded.url === mediaPath(uploaded.id)
        && uploaded.content_type === 'image/webp' && uploaded.width === 36 && uploaded.height === 24
        && /^[0-9a-f]{64}$/.test(uploaded.sha256), 'PHOTO_UPLOAD_PROJECTION_INVALID')
      photoId = uploaded.id; digest = uploaded.sha256
      await entry(author, authorWall, fixture.crossPost)
      const posted = await write(author.page, wallPath(ownerCode), 'POST', 201, () => button(authorWall, author.t.submit).click())
      check(uuid.test(posted.id) && posted.status === 'pending' && posted.body === fixture.crossPost
        && posted.media_ids.length === 1 && posted.media_ids[0] === photoId, 'PENDING_POST_MISMATCH')
      crossPostId = posted.id
      await visit(reader, wallURL(ownerCode))
      readerWall = panel(reader.page, reader.t.wall)
      await exactText(readerWall, fixture.ownPost).waitFor()
      check(await exactText(readerWall, fixture.crossPost).count() === 0
        && await readerWall.getByRole('img', { name: reader.t.photoAlt, exact: true }).count() === 0,
      'PENDING_BODY_OR_PHOTO_VISIBLE')
      const visible = await read(reader, wallPath(ownerCode))
      check(visible.items.length === 1 && visible.items[0].body === fixture.ownPost, 'PENDING_POST_API_EXPOSED')
      await read(reader, mediaPath(photoId), 404)
      await read(reader, commentsPath(crossPostId), 404)
    })

    await run('wall_owner_approves_es', async () => {
      await foreground(owner.page)
      await button(ownerWall, owner.t.refresh).click()
      const article = postArticle(ownerWall, fixture.crossPost)
      await exactText(article, owner.t.pending).waitFor()
      const approved = await write(owner.page, approvePath(crossPostId), 'POST', 200,
        () => button(article, owner.t.approve).click())
      check(approved.id === crossPostId && approved.status === 'visible', 'APPROVAL_MISMATCH')
      await exactText(article, owner.t.visiblePost).waitFor()
      await foreground(reader.page)
      await button(readerWall, reader.t.refresh).click()
      const visibleArticle = postArticle(readerWall, fixture.crossPost)
      await exactText(visibleArticle, fixture.crossPost).waitFor()
      await attachment(reader, visibleArticle)
      check(await read(reader, mediaPath(photoId), 200, true) === digest, 'APPROVED_MEDIA_DIGEST_MISMATCH')
    })

    await run('member_comments_on_exact_photo_en', async () => {
      const article = postArticle(readerWall, fixture.crossPost)
      await button(article, reader.t.comments).click()
      const selector = article.getByLabel(reader.t.commentContext, { exact: true })
      await selector.selectOption({ label: reader.t.photo })
      check(await selector.inputValue() === photoId, 'COMMENT_PHOTO_SELECTION_MISMATCH')
      await entry(reader, article, fixture.comment)
      const saved = await write(reader.page, commentsPath(crossPostId), 'POST', 201,
        () => button(article, reader.t.submit).click())
      check(uuid.test(saved.id) && saved.body === fixture.comment && saved.media_id === photoId,
        'EXACT_PHOTO_COMMENT_NOT_SAVED')
      await exactText(article, fixture.comment).waitFor()
    })

    await run('block_revokes_profile_wall_comment_and_photo', async () => {
      await foreground(owner.page)
      const privacy = panel(owner.page, owner.t.privacy)
      await privacy.getByLabel(owner.t.code, { exact: true }).fill(String(fixture.codes.reader))
      const blocked = await write(owner.page, blockPath(fixture.codes.reader), 'PUT', 200,
        () => button(privacy, owner.t.block).click())
      check(blocked.blocked === true, 'EXACT_BLOCK_NOT_SAVED')
      const denial = reader.page.waitForResponse(response => endpoint(response, profilePath(ownerCode)) && response.status() === 404)
      await foreground(reader.page)
      await reader.page.evaluate(() => window.dispatchEvent(new Event('focus')))
      check(noStore(await denial), 'LIVE_PROFILE_DENIAL_HEADERS')
      await exactText(reader.page, reader.t.denied).waitFor()
      await readerWall.waitFor({ state: 'detached' })
      check(await exactText(reader.page, fixture.status).count() === 0 && await exactText(reader.page, fixture.comment).count() === 0
        && await reader.page.getByRole('img', { name: reader.t.photoAlt, exact: true }).count() === 0,
      'BLOCKED_VIEWER_PRIVATE_DOM_RETAINED')
      for (const path of [profilePath(ownerCode), wallPath(ownerCode), commentsPath(crossPostId), mediaPath(photoId)]) {
        await read(reader, path, 404)
      }
      const retained = await read(owner, commentsPath(crossPostId))
      check(retained.items.length === 1 && retained.items[0].body === fixture.comment
        && await read(author, mediaPath(photoId), 200, true) === digest, 'AUTHORIZED_HISTORY_OR_UPLOADER_BYTES_LOST')
    })

    await run('anonymous_cannot_read', async () => {
      const guest = await session(browser, null, 'en')
      for (const path of [profilePath(ownerCode), wallPath(ownerCode), commentsPath(crossPostId), mediaPath(photoId)]) {
        await read(guest, path, 401)
      }
    })

    check(result.writes === 7 && result.unexpected_mutations === 0 && !result.external_navigation,
      'BROWSER_CONTAINMENT_OR_WRITE_COUNT_MISMATCH')
  } finally {
    await Promise.all([...contexts].map(context => context.close()))
    await browser.close()
  }
}

try {
  await main()
  result.status = 'PASS'
} catch (error) {
  const code = error instanceof CheckError ? error.message : error?.name === 'TimeoutError' ? 'TIMEOUT' : 'UNEXPECTED'
  result.failed_check = activeCheck
  result.error = code
  const line = String(error?.stack ?? '').match(/run-member-community-browser\.mjs:(\d+):\d+/)?.[1]
  if (line) result.line = Number(line)
  process.exitCode = 1
}
console.log('MEMBER_COMMUNITY_BROWSER_RESULT ' + JSON.stringify(result))
