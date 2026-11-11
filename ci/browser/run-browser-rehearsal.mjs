import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'
import { verifyShareContact } from './verify-share-contact.mjs'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const landing = 'http://127.0.0.1:8917'
const management = 'https://management.kaobahotel.com/'
const actors = { admin: 'admin', realtor: 'realtor', owner: 'owner', member: 'buyer', outsider: 'buyer' }
const en = {
  operations: 'Email operations',
  jobs: 'Recorded email jobs',
  management: 'Kaoba management portal',
  denied: 'This record is unavailable or your current account no longer has access.',
  unavailable: 'This section is unavailable. Open your tools to choose a working destination.',
  photo: 'Private photo attached to this entry',
}
const es = {
  operations: 'Operaciones de correo',
  jobs: 'Trabajos de correo registrados',
  management: 'Portal de administración de Kaoba',
}
const noStore = 'no-store'
const timeout = 10_000
const mobile = { width: 390, height: 844 }
const desktop = { width: 1366, height: 900 }
const scope = process.env.ILDR_BROWSER_SCOPE
const focusedChecks = new Set(['admin_contact_share_drafts_desktop_and_mobile', 'browser_never_navigated_outside_loopback'])
const results = { scope: 'isolated_synthetic_browser', status: 'FAIL', checks: [], blocked_external_requests: 0 }

class CheckFailed extends Error {}

function check(ok, code) {
  if (!ok) throw new CheckFailed(code)
}

function exactKeys(value, names) {
  return value && !Array.isArray(value) && typeof value === 'object'
    && Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name))
}

async function readFixtures() {
  const path = process.env.ILDR_BROWSER_FIXTURES
  check(path, 'FIXTURE_FILE_REQUIRED')
  const stat = await lstat(path)
  check(stat.isFile() && stat.size > 0 && stat.size <= 12_000, 'FIXTURE_FILE_INVALID')
  const fixture = JSON.parse(await readFile(path, 'utf8'))
  check(exactKeys(fixture, ['accounts', 'project', 'shareLeadId']), 'FIXTURE_SHAPE_INVALID')
  check(exactKeys(fixture.accounts, Object.keys(actors)), 'FIXTURE_ROLES_INVALID')
  for (const name of Object.keys(actors)) {
    const account = fixture.accounts[name]
    check(exactKeys(account, ['email', 'password']) && account.email === `browser-${name}@example.com`
      && typeof account.password === 'string' && account.password.length >= 8
      && account.password.length <= 200, 'SYNTHETIC_ACCOUNT_REQUIRED')
  }
  const project = fixture.project
  check(exactKeys(project, ['id', 'title', 'mediaId', 'mediaSha256', 'unattachedMediaId', 'memberUserCode', 'updateText']),
    'PROJECT_FIXTURE_SHAPE_INVALID')
  const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
  const ids = [project.id, project.mediaId, project.unattachedMediaId, fixture.shareLeadId]
  check(ids.every(id => typeof id === 'string' && uuid.test(id)) && new Set(ids).size === ids.length
    && typeof project.mediaSha256 === 'string' && /^[0-9a-f]{64}$/.test(project.mediaSha256)
    && Number.isInteger(project.memberUserCode) && project.memberUserCode > 0
    && typeof project.title === 'string' && project.title.length > 0 && project.title.length <= 180
    && typeof project.updateText === 'string' && project.updateText.length > 0 && project.updateText.length <= 4000,
  'PROJECT_FIXTURE_VALUES_INVALID')
  return fixture
}

function endpoint(response, path, method = 'GET') {
  const url = new URL(response.url())
  return url.origin === api && url.pathname === path && response.request().method() === method
}

async function localContext(browser, viewport) {
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' })
  context.setDefaultTimeout(timeout)
  context.setDefaultNavigationTimeout(timeout)
  context.on('request', request => {
    const url = new URL(request.url())
    if (request.isNavigationRequest() && ![dashboard, api, landing].includes(url.origin)) {
      results.blocked_external_navigation = true
    }
  })
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.isNavigationRequest() && url.pathname === '/login'
      && (url.origin === dashboard || url.origin === landing)) {
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
        body: '<!doctype html><html lang="en"><title>Local sign-in destination</title><h1>Local sign-in destination</h1></html>' })
    }
    if (url.origin === dashboard || url.origin === api) return route.continue()
    results.blocked_external_requests += 1
    if (request.isNavigationRequest()) results.blocked_external_navigation = true
    return route.abort('blockedbyclient')
  })
  return context
}

async function enableIsolatedOnlineSignal(context) {
  // A loopback-only namespace can look offline to Chromium despite reachable fixtures.
  // Only the exact synthetic dashboard document receives the application-facing signal.
  await context.addInitScript(origin => {
    if (location.origin !== origin) return
    const raw = navigator.onLine
    let applied = false
    if (!raw) {
      try {
        Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
        applied = true
      } catch { /* The harness verifies the resulting signal before accepting the check. */ }
    }
    Object.defineProperty(window, '__ildrBrowserRehearsalOnline', { value: Object.freeze({ raw, applied }) })
  }, dashboard)
}

async function signIn(context, account) {
  const response = await context.request.post(`${api}/auth/login`, {
    data: account,
    headers: { Origin: dashboard },
    maxRedirects: 0,
    timeout,
  })
  check(response.status() === 200, 'FIXTURE_LOGIN_FAILED')
  const cookie = (await context.cookies(api)).find(cookie => cookie.name === 'ildr_token')
  check(cookie?.httpOnly && cookie.domain === '127.0.0.1' && cookie.path === '/', 'REAL_LOGIN_COOKIE_REQUIRED')
  await response.dispose()
}

async function verifyIdentity(response, account, name) {
  check(response.status() === 200, 'BROWSER_IDENTITY_RESPONSE_FAILED')
  const user = await response.json()
  check(user.email === account.email && user.role === actors[name], 'BROWSER_ROLE_MISMATCH')
}

async function openSignedIn(page, path, account, name) {
  const [identity] = await Promise.all([
    page.waitForResponse(response => endpoint(response, '/auth/me')),
    page.goto(`${dashboard}${path}`, { waitUntil: 'domcontentloaded' }),
  ])
  await verifyIdentity(identity, account, name)
  await page.locator('main').waitFor({ state: 'visible' })
  check(await page.evaluate(() => !document.cookie.includes('ildr_token=')), 'LOGIN_COOKIE_VISIBLE_TO_JS')
}

function managementLink(page, language) {
  return page.locator('aside nav').getByRole('link', { name: language.management, exact: true })
}

async function assertManagementLink(link) {
  await link.waitFor({ state: 'visible' })
  const relations = (await link.getAttribute('rel') ?? '').split(/\s+/)
  check(await link.getAttribute('href') === management && await link.getAttribute('target') === '_blank'
    && relations.includes('noopener') && relations.includes('noreferrer'), 'MANAGEMENT_LINK_ATTRIBUTES')
}

async function openDrawer(page) {
  await page.locator('main button:has(svg.lucide-menu)').click()
  await page.waitForFunction(() => {
    const bounds = document.querySelector('aside')?.getBoundingClientRect()
    return Boolean(bounds && bounds.left >= -1 && bounds.right <= window.innerWidth + 1)
  })
}

async function closeDrawer(page) {
  await page.locator('aside button:has(svg.lucide-x)').click()
  await page.waitForFunction(() => (document.querySelector('aside')?.getBoundingClientRect().right ?? 1) <= 1)
}

async function assertFits(page, locator, drawer = false) {
  await locator.scrollIntoViewIfNeeded()
  const fits = await locator.evaluate((element, insideDrawer) => {
    const r = element.getBoundingClientRect()
    const withinViewport = r.width > 0 && r.left >= -1 && r.right <= window.innerWidth + 1
    const withoutHorizontalOverflow = document.documentElement.scrollWidth <= window.innerWidth + 1
    if (!insideDrawer) return withinViewport && withoutHorizontalOverflow
    const nav = element.closest('nav')?.getBoundingClientRect()
    return withinViewport && withoutHorizontalOverflow && Boolean(nav && r.top >= nav.top - 1 && r.bottom <= nav.bottom + 1)
  }, drawer)
  check(fits, 'MOBILE_CONTENT_OVERFLOW')
}

function projectApi(id) {
  return `/property-network/projects/${id}`
}

function mediaApi(id) {
  return `/property-network/media/${id}`
}

function projectPage(project) {
  return `/property-network?tab=projects&project=${project.id}`
}

function watchPrivateProject(page, project, actor, action) {
  const evidence = { actor, action, visible_before_focus: 0, visible_after_focus: 0,
    visible_after_navigation: 0, online_native: -1, online_effective: -1, online_override_applied: 0,
    project_requests: 0, project_status: 0, media_requests: 0, media_status: 0 }
  const watched = new Map([[projectApi(project.id), 'project'], [mediaApi(project.mediaId), 'media']])
  function requestKind(request) {
    const url = new URL(request.url())
    return url.origin === api && request.method() === 'GET' ? watched.get(url.pathname) : undefined
  }
  function onRequest(request) {
    const kind = requestKind(request)
    if (kind) evidence[`${kind}_requests`] += 1
  }
  function onResponse(response) {
    const kind = requestKind(response.request())
    if (kind) evidence[`${kind}_status`] = response.status()
  }
  page.on('request', onRequest)
  page.on('response', onResponse)
  return { evidence, stop: () => { page.off('request', onRequest); page.off('response', onResponse) } }
}

async function foreground(page, evidence) {
  evidence.visible_before_focus = Number(await page.evaluate(() => document.visibilityState === 'visible'))
  await page.bringToFront()
  await page.waitForFunction(() => document.visibilityState === 'visible')
  evidence.visible_after_focus = 1
}

async function recordOnlineSignal(page, evidence, afterNavigation = false) {
  const signal = await page.evaluate(() => {
    const initial = window.__ildrBrowserRehearsalOnline
    return { native: initial ? Number(initial.raw) : -1,
      effective: Number(navigator.onLine), override: Number(Boolean(initial?.applied)),
      visible: Number(document.visibilityState === 'visible') }
  })
  evidence.online_native = signal.native
  evidence.online_effective = signal.effective
  evidence.online_override_applied = signal.override
  if (afterNavigation) {
    evidence.visible_after_navigation = signal.visible
    const summary = results.isolated_online_signal ??= { observed_pages: 0, native_online_pages: 0, override_pages: 0 }
    summary.observed_pages += 1
    summary.native_online_pages += Number(signal.native === 1)
    summary.override_pages += signal.override
  }
  check(signal.native !== -1 && signal.effective === 1 && signal.visible === 1, 'PRIVATE_PROJECT_BROWSER_NOT_VISIBLE_ONLINE')
}

async function probeMedia(page, id, expectedStatus) {
  const result = await page.evaluate(async url => {
    const response = await fetch(url, { credentials: 'include', redirect: 'error', cache: 'no-store' })
    const type = response.headers.get('content-type') ?? ''
    const bytes = new Uint8Array(await response.arrayBuffer())
    return {
      status: response.status,
      type,
      cache: response.headers.get('cache-control'),
      pragma: response.headers.get('pragma'),
      isWebp: bytes[0] === 82 && bytes[1] === 73 && bytes[2] === 70 && bytes[3] === 70
        && bytes[8] === 87 && bytes[9] === 69 && bytes[10] === 66 && bytes[11] === 80,
    }
  }, `${api}${mediaApi(id)}`)
  check(result.status === expectedStatus && result.cache === noStore && result.pragma === 'no-cache',
    'PRIVATE_PHOTO_STATUS_OR_CACHE')
  check(expectedStatus === 200 ? result.type === 'image/webp' && result.isWebp
    : result.type.startsWith('application/json') && !result.isWebp, 'PRIVATE_PHOTO_BYTES_OR_DENIAL')
}

async function assertRenderedPhoto(page, project) {
  const photo = page.getByRole('img', { name: en.photo, exact: true })
  await photo.waitFor({ state: 'visible' })
  await page.waitForFunction(label => {
    const image = Array.from(document.images).find(item => item.alt === label)
    return Boolean(image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0)
  }, en.photo)
  await assertFits(page, photo)
  const source = await photo.getAttribute('src')
  check(source?.startsWith(`blob:${dashboard}/`), 'PRIVATE_IMAGE_MUST_USE_LOCAL_BLOB')
  const digest = await page.evaluate(async url => {
    const response = await fetch(url, { redirect: 'error' })
    const buffer = await response.arrayBuffer()
    const hash = await crypto.subtle.digest('SHA-256', buffer)
    return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
  }, source)
  check(digest === project.mediaSha256, 'PRIVATE_IMAGE_BLOB_SHA_MISMATCH')
  return source
}

async function assertBlobGone(page, source) {
  await page.waitForFunction(async url => {
    try {
      await fetch(url, { redirect: 'error' })
      return false
    } catch {
      return true
    }
  }, source)
}

async function main() {
  let current = 'fixture_and_browser'
  let browser
  let privateEvidence
  const contexts = []
  try {
    check(scope === undefined || scope === 'contact_share', 'UNKNOWN_BROWSER_SCOPE')
    if (scope) results.suite = scope
    const { accounts, project, shareLeadId } = await readFixtures()
    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.ILDR_CHROMIUM_PATH || undefined,
      timeout: 20_000,
      proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
      args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
    })
    async function context(viewport, name) {
      const value = await localContext(browser, viewport)
      contexts.push(value)
      if (['owner', 'member', 'outsider'].includes(name)) await enableIsolatedOnlineSignal(value)
      if (name) await signIn(value, accounts[name])
      return value
    }
    async function step(name, action) {
      if (scope && !focusedChecks.has(name)) return
      current = name
      privateEvidence = undefined
      await action()
      results.checks.push(name)
    }

    await step('anonymous_route_and_media', async () => {
      const anonymous = await context(mobile)
      const denied = await anonymous.request.get(`${api}${mediaApi(project.mediaId)}`, { maxRedirects: 0, timeout })
      check(denied.status() === 401 && denied.headers()['cache-control'] === noStore, 'ANONYMOUS_MEDIA_EXPOSED')
      await denied.dispose()
      const page = await anonymous.newPage()
      await page.goto(`${dashboard}/communications?workspace=admin`, { waitUntil: 'commit' })
      await page.waitForURL(url => [dashboard, landing].includes(url.origin) && url.pathname === '/login')
      await page.getByRole('heading', { name: 'Local sign-in destination', exact: true }).waitFor()
      check(await managementLink(page, en).count() === 0, 'ANONYMOUS_ADMIN_LINK_EXPOSED')
      await anonymous.close()
    })

    await step('admin_desktop_and_workspaces', async () => {
      const admin = await context(desktop, 'admin')
      const page = await admin.newPage()
      const [operations] = await Promise.all([
        page.waitForResponse(response => endpoint(response, '/admin/communications')),
        openSignedIn(page, '/communications?workspace=admin', accounts.admin, 'admin'),
      ])
      check(operations.status() === 200 && operations.headers()['cache-control'] === noStore, 'ADMIN_OPERATIONS_READ_FAILED')
      const report = await operations.json()
      check(Array.isArray(report.items) && Number.isInteger(report.total) && report.enabled === false
        && report.customer_messages_enabled === false, 'ISOLATED_OPERATIONS_RESPONSE_REQUIRED')
      await page.getByRole('region', { name: en.jobs, exact: true }).waitFor()
      await assertManagementLink(managementLink(page, en))
      const workspace = page.locator('#dashboard-workspace')
      for (const other of ['Buyer', 'Owner', 'Realtor']) {
        await workspace.selectOption(other)
        await page.waitForURL(url => url.origin === dashboard && url.searchParams.get('workspace') === other.toLowerCase())
        await managementLink(page, en).waitFor({ state: 'detached' })
        await page.waitForFunction(expected => document.querySelector('#dashboard-workspace')?.value === expected, other)
        check(await workspace.inputValue() === other, 'ADMIN_WORKSPACE_SELECTION_FAILED')
      }
      await admin.close()
    })

    await step('admin_mobile_drawer_and_language', async () => {
      const admin = await context(mobile, 'admin')
      const page = await admin.newPage()
      await openSignedIn(page, '/communications?workspace=admin', accounts.admin, 'admin')
      await page.getByRole('region', { name: en.jobs, exact: true }).waitFor()
      await openDrawer(page)
      const english = managementLink(page, en)
      await assertManagementLink(english)
      await assertFits(page, english, true)
      await page.locator('aside').getByRole('button', { name: /^Language\b/ }).click()
      const spanish = managementLink(page, es)
      await assertManagementLink(spanish)
      await assertFits(page, spanish, true)
      check(await page.evaluate(() => localStorage.getItem('ildr_lang') === 'es'), 'MOBILE_SPANISH_NOT_PERSISTED')
      await closeDrawer(page)
      await page.getByRole('region', { name: es.jobs, exact: true }).waitFor()
      await assertFits(page, page.getByRole('region', { name: es.jobs, exact: true }))
      await admin.close()
    })

    await step('realtor_mobile_admin_route_denied', async () => {
      const realtor = await context(mobile, 'realtor')
      const page = await realtor.newPage()
      let pageOperations = 0
      page.on('request', request => {
        const url = new URL(request.url())
        if (url.origin === api && url.pathname === '/admin/communications') pageOperations += 1
      })
      await openSignedIn(page, '/communications?workspace=admin', accounts.realtor, 'realtor')
      await page.getByText(en.unavailable, { exact: true }).waitFor()
      await openDrawer(page)
      check(await managementLink(page, en).count() === 0 && await page.locator('#dashboard-workspace').count() === 0
        && await page.locator('aside nav').getByRole('button', { name: en.operations, exact: true }).count() === 0
        && pageOperations === 0, 'REALTOR_ADMIN_UI_OR_REQUEST_EXPOSED')
      const denied = await realtor.request.get(`${api}/admin/communications`, { maxRedirects: 0, timeout })
      check(denied.status() === 403 && denied.headers()['cache-control'] === noStore, 'REALTOR_ADMIN_API_EXPOSED')
      await denied.dispose()
      await realtor.close()
    })

    await step('admin_contact_share_drafts_desktop_and_mobile', async () => {
      const lead = { id: shareLeadId, name: 'Browser Share Contact', email: 'browser-share+ci@example.com',
        phone: '+12025550119', message: 'Synthetic private lead note sentinel' }
      const desktopAdmin = await context(desktop, 'admin')
      const desktopPage = await desktopAdmin.newPage()
      await desktopPage.bringToFront()
      await openSignedIn(desktopPage, '/communications?workspace=admin', accounts.admin, 'admin')
      const englishShare = await verifyShareContact(desktopPage, lead)
      await desktopAdmin.close()

      const mobileAdmin = await context(mobile, 'admin')
      const mobilePage = await mobileAdmin.newPage()
      await mobilePage.bringToFront()
      await openSignedIn(mobilePage, '/communications?workspace=admin', accounts.admin, 'admin')
      await openDrawer(mobilePage)
      await mobilePage.locator('aside').getByRole('button', { name: /^Language\b/ }).click()
      await closeDrawer(mobilePage)
      await mobilePage.getByRole('region', { name: es.jobs, exact: true }).waitFor()
      const spanishShare = await verifyShareContact(mobilePage, lead, { language: 'es', mobile: true })
      await mobileAdmin.close()
      results.contact_share = { english_desktop: englishShare, spanish_mobile: spanishShare }
    })

    let ownerPage, memberPage, memberBlob
    await step('owner_and_member_private_photo', async () => {
      for (const name of ['owner', 'member']) {
        const session = await context(mobile, name)
        const page = await session.newPage()
        if (name === 'owner') ownerPage = page
        else memberPage = page
        const watcher = watchPrivateProject(page, project, name, 'initial_read')
        privateEvidence = watcher.evidence
        try {
          await foreground(page, privateEvidence)
          const [projectResponse, mediaResponse] = await Promise.all([
            page.waitForResponse(response => endpoint(response, projectApi(project.id))),
            page.waitForResponse(response => endpoint(response, mediaApi(project.mediaId))),
            openSignedIn(page, projectPage(project), accounts[name], name)
              .then(() => recordOnlineSignal(page, watcher.evidence, true)),
          ])
          check(projectResponse.status() === 200 && mediaResponse.status() === 200
            && mediaResponse.headers()['cache-control'] === noStore && mediaResponse.headers()['content-type'] === 'image/webp',
          'PROJECT_OR_MEDIA_RESPONSE_FAILED')
          const record = await projectResponse.json()
          check(record.id === project.id && record.my_role === (name === 'owner' ? 'owner' : 'viewer')
            && (name === 'owner' || record.can_update === false), 'PROJECT_MEMBERSHIP_MISMATCH')
          await page.getByRole('heading', { name: project.title, exact: true }).waitFor()
          await page.getByText(project.updateText, { exact: true }).waitFor()
          const blob = await assertRenderedPhoto(page, project)
          if (name === 'member') memberBlob = blob
          await probeMedia(page, project.unattachedMediaId, name === 'owner' ? 200 : 404)
        } finally {
          watcher.stop()
        }
      }
    })

    await step('outsider_cannot_see_project_or_media', async () => {
      const outsider = await context(mobile, 'outsider')
      const page = await outsider.newPage()
      const watcher = watchPrivateProject(page, project, 'outsider', 'denied_read')
      privateEvidence = watcher.evidence
      try {
        await foreground(page, privateEvidence)
        const [projectResponse] = await Promise.all([
          page.waitForResponse(response => endpoint(response, projectApi(project.id))),
          openSignedIn(page, projectPage(project), accounts.outsider, 'outsider')
            .then(() => recordOnlineSignal(page, watcher.evidence, true)),
        ])
        check(projectResponse.status() === 404 && projectResponse.headers()['cache-control'] === noStore, 'OUTSIDER_PROJECT_EXPOSED')
        await page.getByText(en.denied, { exact: true }).waitFor()
        check(await page.getByRole('heading', { name: project.title, exact: true }).count() === 0
          && await page.getByText(project.updateText, { exact: true }).count() === 0
          && await page.getByRole('img', { name: en.photo, exact: true }).count() === 0, 'OUTSIDER_PRIVATE_UI_EXPOSED')
        await probeMedia(page, project.mediaId, 404)
        await probeMedia(page, project.unattachedMediaId, 404)
        await outsider.close()
      } finally {
        watcher.stop()
      }
    })

    await step('owner_revocation_removes_member_photo', async () => {
      const owner = watchPrivateProject(ownerPage, project, 'owner', 'revoke_member')
      const member = watchPrivateProject(memberPage, project, 'member', 'revoked_read')
      privateEvidence = owner.evidence
      try {
        await foreground(ownerPage, owner.evidence)
        await recordOnlineSignal(ownerPage, owner.evidence)
        await ownerPage.getByRole('navigation', { name: 'Project sections', exact: true })
          .getByRole('button', { name: 'Project team', exact: true }).click()
        const memberRow = ownerPage.locator('main li').filter({ hasText: `#${project.memberUserCode} ·` })
        const revoke = memberRow.getByRole('button', { name: 'Revoke project access', exact: true })
        await revoke.waitFor()
        const revokePath = `${projectApi(project.id)}/members/${project.memberUserCode}/revoke`
        const [revoked] = await Promise.all([
          ownerPage.waitForResponse(response => endpoint(response, revokePath, 'POST')),
          revoke.click(),
        ])
        owner.evidence.revoke_status = revoked.status()
        check(revoked.status() === 200 && revoked.headers()['cache-control'] === noStore, 'OWNER_REVOCATION_FAILED')
        privateEvidence = member.evidence
        // Focus itself can wake the project's read loop, so install the 404 waiter first.
        const [checked] = await Promise.all([
          memberPage.waitForResponse(response => endpoint(response, projectApi(project.id)) && response.status() === 404),
          (async () => {
            await foreground(memberPage, member.evidence)
            await recordOnlineSignal(memberPage, member.evidence)
            await memberPage.getByRole('button', { name: 'Reload current record', exact: true }).click()
          })(),
        ])
        check(checked.headers()['cache-control'] === noStore, 'REVOKED_PROJECT_CACHEABLE')
        await memberPage.getByText(en.denied, { exact: true }).waitFor()
        await memberPage.getByRole('img', { name: en.photo, exact: true }).waitFor({ state: 'detached' })
        await assertBlobGone(memberPage, memberBlob)
        await probeMedia(memberPage, project.mediaId, 404)
        privateEvidence = owner.evidence
        await foreground(ownerPage, owner.evidence)
        await recordOnlineSignal(ownerPage, owner.evidence)
        await probeMedia(ownerPage, project.mediaId, 200)
      } finally {
        owner.stop()
        member.stop()
      }
    })

    await step('browser_never_navigated_outside_loopback', async () => {
      check(!results.blocked_external_navigation, 'UNEXPECTED_NONLOCAL_BROWSER_NAVIGATION')
    })
    results.status = 'PASS'
  } catch (error) {
    results.failed_step = current
    if (privateEvidence) results.private_project_observation = privateEvidence
    results.error_code = error instanceof CheckFailed ? error.message
      : error?.name === 'TimeoutError' ? 'BROWSER_REHEARSAL_TIMEOUT' : 'BROWSER_REHEARSAL_STEP_FAILED'
    if (current === 'admin_contact_share_drafts_desktop_and_mobile'
      && /^SHARE_CONTACT_[A-Z_]+$/.test(error?.message ?? '')) results.error_code = error.message
    const sourceFrame = /run-browser-rehearsal\.mjs:(\d{1,5}):\d{1,4}/.exec(String(error?.stack))
    if (sourceFrame) results.source_line = Number(sourceFrame[1])
    process.exitCode = 1
  } finally {
    const closed = await Promise.allSettled(contexts.map(context => context.close()))
    try {
      if (browser) await browser.close()
      check(!browser?.isConnected(), 'BROWSER_PROCESS_STILL_CONNECTED')
    } catch {
      results.status = 'FAIL'
      results.error_code = 'BROWSER_CLEANUP_FAILED'
      process.exitCode = 1
    }
    if (closed.some(entry => entry.status !== 'fulfilled')) {
      results.status = 'FAIL'
      results.error_code = 'CONTEXT_CLEANUP_FAILED'
      process.exitCode = 1
    }
    process.stdout.write(`BROWSER_REHEARSAL_RESULT ${JSON.stringify(results)}\n`)
  }
}

await main()

