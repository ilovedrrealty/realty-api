import { lstat, readFile } from 'node:fs/promises'
import { chromium } from 'playwright-core'

const dashboard = 'http://127.0.0.1:4175'
const api = 'http://127.0.0.1:8000'
const fixturePath = '/browser-fixtures/fixtures.json'
const roomTitle = 'Synthetic Villa Closing'
const documentTitle = 'Synthetic purchase packet'
const professionalName = 'Browser Closing Professional'
const taskTitle = 'Check the synthetic deed'
const milestoneTitle = 'Synthetic title review'
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/
let step = 'fixture'
const checks = []

class FailedCheck extends Error {}
function check(condition, message) {
  if (!condition) throw new FailedCheck(message)
}

function failureReason(error) {
  if (error instanceof FailedCheck) return error.message
  const message = typeof error?.message === 'string' ? error.message : ''
  if (message.includes('intercepts pointer events')) return 'POINTER_INTERCEPTED'
  if (message.includes('strict mode violation')) return 'LOCATOR_AMBIGUOUS'
  if (error?.name === 'TimeoutError' && !message.includes('locator resolved')) return 'LOCATOR_OR_EVENT_NOT_FOUND'
  return error?.name === 'TimeoutError' ? 'TIMED_OUT' : 'BROWSER_OPERATION_FAILED'
}

async function getFixture() {
  check(process.platform === 'linux' && process.env.GITHUB_ACTIONS === 'true'
    && process.env.GITHUB_REPOSITORY === 'ilovedrrealty/realty-api'
    && process.env.ILDR_BROWSER_FIXTURES === fixturePath, 'OWNED_SYNTHETIC_FIXTURE_REQUIRED')
  const file = await lstat(fixturePath)
  check(file.isFile() && (file.mode & 0o777) === 0o600 && file.size < 4096, 'PRIVATE_FIXTURE_REQUIRED')
  const raw = JSON.parse(await readFile(fixturePath, 'utf8'))
  check(uuid.test(raw.shareLeadId) && uuid.test(raw.foreignLeadId) && raw.shareLeadId !== raw.foreignLeadId
    && Number.isSafeInteger(raw.transactionProfessionalCode) && raw.transactionProfessionalCode > 0
    && /^[A-Za-z0-9+/=]{65,512}$/.test(raw.transactionPng), 'EXACT_FIXTURE_DATA_REQUIRED')
  for (const [account, expected] of [[raw.accounts.realtor, 'browser-realtor@example.com'],
    [raw.accounts.buyer, 'browser-buyer@example.com'], [raw.transactionProfessional, 'browser-closing-professional@example.com'],
    [raw.transactionUnassigned, 'browser-unassigned-closing@example.com']]) {
    check(account?.email === expected && /^[A-Za-z0-9_-]{32}$/.test(account.password), 'SYNTHETIC_CREDENTIAL_SHAPE_REQUIRED')
  }
  return raw
}

async function request(context, path, { method = 'GET', data, expected = 200, noStore = true, bytes = false } = {}) {
  const response = await context.request.fetch(api + path, {
    method, data, headers: { Origin: dashboard }, maxRedirects: 0, timeout: 12_000,
  })
  try {
    check(response.status() === expected, 'NATIVE_HTTP_' + response.status())
    if (noStore) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
    if (bytes) return { data: await response.body(), headers: response.headers() }
    return await response.json()
  } finally {
    await response.dispose()
  }
}

async function session(browser, account, mobile = false) {
  const context = await browser.newContext({
    locale: 'en-US', serviceWorkers: 'block', isMobile: mobile, hasTouch: mobile, acceptDownloads: true,
    viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 },
  })
  context.setDefaultTimeout(12_000)
  context.setDefaultNavigationTimeout(12_000)
  await context.route('**/*', route => [dashboard, api].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort('blockedbyclient'))
  await context.addInitScript(() => {
    if (location.origin === 'http://127.0.0.1:4175') localStorage.setItem('ildr_lang', 'en')
    if (!navigator.onLine) Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => true })
  })
  if (account) {
    await request(context, '/auth/login', { method: 'POST', data: account, noStore: false })
    check((await context.cookies(api)).some(cookie => cookie.name === 'ildr_token' && cookie.httpOnly),
      'REAL_SYNTHETIC_LOGIN_REQUIRED')
  }
  return context
}

function pendingResponse(page, method, pathname) {
  return page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.origin === api && ((typeof pathname === 'string' && url.pathname === pathname)
      || (pathname instanceof RegExp && pathname.test(url.pathname))) && response.request().method() === method
  }, { timeout: 12_000 }).catch(() => null)
}

async function checkedResponse(promise, expected) {
  const response = await promise
  check(response, 'BROWSER_API_RESPONSE_NOT_OBSERVED')
  check(response.status() === expected, 'BROWSER_HTTP_' + response.status())
  if (response.url().includes('/transactions')) check(response.headers()['cache-control'] === 'no-store', 'NO_STORE_REQUIRED')
  return response
}

async function action(page, method, path, expected, control) {
  const result = pendingResponse(page, method, path)
  await control.click()
  return checkedResponse(result, expected)
}

const button = (scope, name) => scope.getByRole('button', { name, exact: true })
const text = (scope, value) => scope.getByText(value, { exact: true })
const label = (scope, name) => scope.getByLabel(name, { exact: true })
const dialog = (page, name) => page.getByRole('dialog', { name, exact: true })

function sections(page) {
  return page.getByRole('group', { name: 'Transaction room sections', exact: true })
}

async function section(page, name) {
  await button(sections(page), name).click()
  return page.getByRole('region', { name, exact: true })
}

function rowFor(scope, textValue) {
  return scope.getByRole('listitem').filter({ hasText: textValue })
}

function dateInDR(offset = 0) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Santo_Domingo',
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(Date.now() + offset * 86_400_000))
  const date = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return date.year + '-' + date.month + '-' + date.day
}

function minimalPdf() {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 36 36] >>']
  let data = '%PDF-1.4\n'
  const offsets = objects.map((object, index) => {
    const offset = Buffer.byteLength(data, 'ascii')
    data += (index + 1) + ' 0 obj\n' + object + '\nendobj\n'
    return offset
  })
  return Buffer.from(data + 'xref\n0 4\n0000000000 65535 f \n'
    + offsets.map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('')
    + 'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + Buffer.byteLength(data, 'ascii') + '\n%%EOF\n', 'ascii')
}

async function dashboardPage(context, route) {
  const page = await context.newPage()
  await page.bringToFront()
  const identity = pendingResponse(page, 'GET', '/auth/me')
  const response = await page.goto(dashboard + route, { waitUntil: 'domcontentloaded' })
  check(response?.ok(), 'ACTUAL_DASHBOARD_BUILD_REQUIRED')
  await checkedResponse(identity, 200)
  return page
}

async function openRoom(page, path, control) {
  await action(page, 'GET', path, 200, control)
  await page.getByRole('heading', { name: roomTitle, exact: true }).waitFor()
}

async function refreshRoom(page, path) {
  await page.bringToFront()
  const pending = pendingResponse(page, 'GET', path)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await checkedResponse(pending, 200)
  await page.getByRole('heading', { name: roomTitle, exact: true }).waitFor()
}

async function readRoom(context, path, expectedRole) {
  const room = await request(context, path)
  check(room.role === expectedRole && room.title === roomTitle && path.endsWith('/' + room.id),
    'EXACT_ROOM_ROLE_REQUIRED')
  return room
}

async function buildRoom(staff, fixture) {
  step = 'lead_open'
  const leadPath = '/transactions/leads/' + fixture.shareLeadId
  const page = await dashboardPage(staff, '/leads?workspace=realtor')
  await text(page.locator('main'), 'Browser Share Contact').filter({ visible: true }).click()
  await action(page, 'GET', leadPath, 200, button(page, 'Transaction room').filter({ visible: true }))
  check((await request(staff, leadPath)).room === null, 'EMPTY_ASSIGNED_LEAD_REQUIRED')
  step = 'room_create'
  await label(page, 'Room title').fill(roomTitle)
  await action(page, 'POST', leadPath, 201, button(page, 'Create private room'))
  const { room } = await request(staff, leadPath)
  check(room?.role === 'realtor' && room.title === roomTitle && uuid.test(room.id), 'EXACT_ROOM_CREATION_REQUIRED')
  const path = '/transactions/' + room.id
  await openRoom(page, path, button(page, 'Open transaction room'))
  checks.push('realtor_creates_from_assigned_lead')
  return { page, path, id: room.id }
}

async function invite(staff, professional, room, fixture) {
  step = 'invitation_create'
  const members = await section(room.page, 'Members')
  await button(members, 'Invite a professional').click()
  const editor = dialog(room.page, 'Invite a professional')
  await editor.getByRole('textbox').fill(String(fixture.transactionProfessionalCode))
  await action(room.page, 'POST', room.path + '/invitations', 200, button(editor, 'Create invitation'))
  await editor.waitFor({ state: 'hidden' })
  const pending = (await readRoom(staff, room.path, 'realtor')).members.find(member => member.display_name === professionalName)
  check(uuid.test(pending?.grant_id ?? '') && pending.state === 'pending', 'PENDING_EXACT_INVITATION_REQUIRED')
  await request(professional, room.path, { expected: 404 })

  step = 'invitation_accept'
  const page = await dashboardPage(professional, '/transactions?workspace=owner')
  await button(page, 'Accept invitation').click()
  const accept = dialog(page, 'Accept invitation')
  await action(page, 'POST', '/transactions/invitations/' + pending.grant_id + '/accept', 200, button(accept, 'Accept and join'))
  await accept.waitFor({ state: 'hidden' })
  await openRoom(page, room.path, button(page, 'Open your new room'))
  await readRoom(professional, room.path, 'professional')
  const member = (await readRoom(staff, room.path, 'realtor')).members.find(item => item.grant_id === pending.grant_id)
  check(member?.state === 'active', 'ACTIVE_EXACT_PROFESSIONAL_REQUIRED')
  checks.push('verified_professional_accepts_invitation')
  return { page, member }
}

async function work(staff, room) {
  const today = dateInDR(), target = dateInDR(3)
  step = 'task_create'
  await refreshRoom(room.page, room.path)
  const tasks = await section(room.page, 'Tasks')
  await button(tasks, 'Add task').click()
  let editor = dialog(room.page, 'Add task')
  await label(editor, 'Task title').fill(taskTitle)
  await label(editor, 'Due date').fill(target)
  await action(room.page, 'POST', room.path + '/tasks', 201, button(editor, 'Save changes'))
  await editor.waitFor({ state: 'hidden' })
  const createdTask = (await readRoom(staff, room.path, 'realtor')).tasks.find(task => task.title === taskTitle)
  check(uuid.test(createdTask?.id ?? '') && createdTask.status === 'open' && createdTask.due_on === target,
    'EXACT_CREATED_TASK_REQUIRED')

  step = 'task_update'
  await button(rowFor(tasks, taskTitle), 'Edit task').click()
  editor = dialog(room.page, 'Edit task')
  await editor.locator('select:has(>option[value="done"])').selectOption('done')
  await action(room.page, 'PATCH', room.path + '/tasks/' + createdTask.id, 200, button(editor, 'Save changes'))
  await editor.waitFor({ state: 'hidden' })
  await text(rowFor(tasks, taskTitle), 'Unassigned · Done').waitFor()

  step = 'milestone_create'
  const milestones = await section(room.page, 'Closing milestones')
  await button(milestones, 'Add closing milestone').click()
  editor = dialog(room.page, 'Add closing milestone')
  await label(editor, 'Milestone title').fill(milestoneTitle)
  await label(editor, 'Target date').fill(target)
  await action(room.page, 'POST', room.path + '/milestones', 201, button(editor, 'Save changes'))
  await editor.waitFor({ state: 'hidden' })
  const created = (await readRoom(staff, room.path, 'realtor')).milestones.find(item => item.title === milestoneTitle)
  check(uuid.test(created?.id ?? '') && created.target_on === target && created.actual_on === null, 'EXACT_MILESTONE_REQUIRED')

  step = 'milestone_update'
  await button(rowFor(milestones, milestoneTitle), 'Edit closing milestone').click()
  editor = dialog(room.page, 'Edit closing milestone')
  await label(editor, 'Actual date').fill(today)
  await action(room.page, 'PUT', room.path + '/milestones/' + created.id, 200, button(editor, 'Save changes'))
  await editor.waitFor({ state: 'hidden' })
  const state = await readRoom(staff, room.path, 'realtor')
  check(state.tasks.some(item => item.id === createdTask.id && item.status === 'done' && item.due_on === target)
    && state.milestones.some(item => item.id === created.id && item.target_on === target && item.actual_on === today),
  'PERSISTED_TASK_AND_MILESTONE_REQUIRED')
  checks.push('task_and_closing_milestone_changed')
}

function documentRow(page) {
  return page.getByRole('article', { name: documentTitle, exact: true })
}

async function upload(page, path, file, number) {
  const heading = number === 1 ? 'Upload document' : 'Upload new version'
  const target = number === 1 ? page : documentRow(page)
  await button(target, heading).click()
  const form = dialog(page, heading)
  if (number === 1) await label(form, 'Document title').fill(documentTitle)
  await form.locator('input[type="file"]').setInputFiles(file)
  const reserve = pendingResponse(page, 'POST', path + '/documents')
  const content = pendingResponse(page, 'PUT', new RegExp('^' + path + '/documents/[a-f0-9-]+/versions/[a-f0-9-]+/content$'))
  await button(form, 'Submit for review').click()
  await checkedResponse(reserve, 200)
  await checkedResponse(content, 200)
  await form.waitFor({ state: 'hidden' })
  await text(page, 'Upload processed.').waitFor()
}

async function uploads(buyer, room, png) {
  step = 'buyer_room'
  const page = await dashboardPage(buyer, '/transactions?workspace=buyer')
  const card = rowFor(page, roomTitle)
  await openRoom(page, room.path, button(card, 'Open transaction room'))
  await readRoom(buyer, room.path, 'buyer')
  await section(page, 'Documents')
  const pdf = { name: 'synthetic-offer.pdf', mimeType: 'application/pdf', buffer: minimalPdf() }
  const image = { name: 'synthetic-attachment.png', mimeType: 'image/png', buffer: png }
  step = 'pdf_upload'
  await upload(page, room.path, pdf, 1)
  step = 'png_upload'
  await upload(page, room.path, image, 2)
  const result = await request(buyer, room.path + '/documents')
  const doc = result.items.find(item => item.title === documentTitle)
  const first = doc?.versions.find(item => item.number === 1)
  const latest = doc?.versions.find(item => item.number === 2)
  check(uuid.test(doc?.id ?? '') && uuid.test(first?.id ?? '') && uuid.test(latest?.id ?? '')
    && first.id !== latest.id && first.file_name === pdf.name && latest.file_name === image.name
    && first.status === 'submitted' && latest.status === 'submitted'
    && !first.can_download && !latest.can_download, 'EXACT_UNAPPROVED_VERSIONS_REQUIRED')
  const versionBase = room.path + '/documents/' + doc.id + '/versions/'
  const firstPath = versionBase + first.id, latestPath = versionBase + latest.id
  await request(buyer, firstPath + '/download', { expected: 404 })
  checks.push('buyer_uploads_pdf_and_png_as_exact_versions')
  return { page, first, latest, firstPath, latestPath, pdf, image }
}

async function approve(page, version, path, grant) {
  await button(documentRow(page), 'Review version ' + version.number).click()
  const review = dialog(page, 'Review document version')
  await review.getByText('Version ' + version.number).waitFor()
  const buyer = label(review, 'Allow the buyer to download')
  if (grant) {
    await buyer.check()
    await review.locator('select:has(>option[value="' + grant + '"])').selectOption(grant)
  } else check(!await buyer.isChecked(), 'STAFF_ONLY_DEFAULT_REQUIRED')
  await button(review, 'Review approval').click()
  const response = await action(page, 'POST', path + '/review', 200, button(review, 'Approve version'))
  const patch = response.request().postDataJSON()
  check(patch.status === 'approved' && Number.isSafeInteger(patch.expected_version)
    && patch.share_with_buyer === Boolean(grant) && patch.professional_grant_id === (grant || null),
  'EXACT_REVIEW_SELECTION_REQUIRED')
  await review.waitFor({ state: 'hidden' })
}

async function verifyBytes(page, path, file) {
  const pending = page.waitForEvent('download', { timeout: 12_000 }).catch(() => null)
  const response = await action(page, 'GET', path + '/download', 200, button(documentRow(page), 'Download version 1'))
  check(response.headers()['content-type'] === file.mimeType
    && response.headers()['content-disposition']?.includes(file.name), 'EXACT_ATTACHMENT_HEADERS_REQUIRED')
  const download = await pending
  check(download?.suggestedFilename() === file.name, 'EXACT_ATTACHMENT_NAME_REQUIRED')
  const stream = await download.createReadStream()
  const chunks = []
  let length = 0
  for await (const chunk of stream) {
    length += chunk.length
    check(length <= 16 * 1024, 'SYNTHETIC_DOWNLOAD_TOO_LARGE')
    chunks.push(chunk)
  }
  check(Buffer.concat(chunks).equals(file.buffer), 'EXACT_SYNTHETIC_DOWNLOAD_REQUIRED')
}

async function main() {
  const fixture = await getFixture()
  const browser = await chromium.launch({
    headless: true, timeout: 20_000, proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1' },
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run'],
  })
  try {
    step = 'auth'
    const staff = await session(browser, fixture.accounts.realtor)
    const buyer = await session(browser, fixture.accounts.buyer, true)
    const professional = await session(browser, fixture.transactionProfessional, true)
    const unassigned = await session(browser, fixture.transactionUnassigned)
    const anonymous = await session(browser, null)
    step = 'scope'
    await request(anonymous, '/transactions', { expected: 401 })
    await request(unassigned, '/transactions/leads/' + fixture.shareLeadId, { expected: 404 })
    await request(staff, '/transactions/leads/' + fixture.foreignLeadId, {
      method: 'POST', data: { title: 'Must be rejected by fixture scope' }, expected: 405,
    })

    const room = await buildRoom(staff, fixture)
    const participant = await invite(staff, professional, room, fixture)
    await work(staff, room)
    const docs = await uploads(buyer, room, Buffer.from(fixture.transactionPng, 'base64'))
    step = 'staff_document_panel'
    await refreshRoom(room.page, room.path)
    await section(room.page, 'Documents')
    await text(documentRow(room.page), 'Version 2').waitFor()
    step = 'pdf_review'
    await approve(room.page, docs.first, docs.firstPath, participant.member.grant_id)
    step = 'png_review'
    await refreshRoom(room.page, room.path)
    await section(room.page, 'Documents')
    await approve(room.page, docs.latest, docs.latestPath, null)
    checks.push('staff_reviews_exact_version_and_explicit_audience')

    step = 'buyer_download'
    await refreshRoom(docs.page, room.path)
    await section(docs.page, 'Documents')
    await verifyBytes(docs.page, docs.firstPath, docs.pdf)
    step = 'withheld'
    await text(documentRow(docs.page), 'Version 2').waitFor()
    check(await button(documentRow(docs.page), 'Download version 2').count() === 0, 'WITHHELD_DOWNLOAD_CONTROL_VISIBLE')
    await request(buyer, docs.latestPath + '/download', { expected: 404 })
    await request(unassigned, room.path, { expected: 404 })
    await request(unassigned, docs.firstPath + '/download', { expected: 404 })
    const staffOnly = await request(staff, docs.latestPath + '/download', { bytes: true })
    check(staffOnly.data.equals(docs.image.buffer) && staffOnly.headers['content-type'] === docs.image.mimeType,
      'EXACT_STAFF_ONLY_PNG_REQUIRED')

    step = 'professional_download'
    await refreshRoom(participant.page, room.path)
    await section(participant.page, 'Documents')
    await verifyBytes(participant.page, docs.firstPath, docs.pdf)
    await request(professional, docs.latestPath + '/download', { expected: 404 })
    checks.push('permitted_downloads_preserve_exact_bytes')

    step = 'staff_audit'
    await room.page.bringToFront()
    const history = await section(room.page, 'History')
    await text(history, 'Document version approved').first().waitFor()
    const staffAudit = await request(staff, room.path + '/history')
    check([docs.first, docs.latest].every(version => staffAudit.items.some(event =>
      event.action === 'document_version_approved' && event.target_id === version.id)), 'EXACT_STAFF_DOCUMENT_AUDIT_REQUIRED')
    step = 'member_audit'
    for (const context of [buyer, professional]) {
      const memberAudit = await request(context, room.path + '/history')
      check(memberAudit.items.some(event => event.action === 'task_updated')
        && !memberAudit.items.some(event => event.action.startsWith('document_')), 'PRIVATE_DOCUMENT_AUDIT_VISIBLE')
    }
    const viewport = await docs.page.locator('main').evaluate(node => ({
      touches: navigator.maxTouchPoints, right: node.getBoundingClientRect().right, width: window.innerWidth,
    }))
    check(viewport.touches > 0 && viewport.right <= viewport.width + 1, 'MOBILE_ROOM_LAYOUT_REQUIRED')

    step = 'revoke'
    const members = await section(room.page, 'Members')
    await button(rowFor(members, professionalName), 'Revoke professional access').click()
    const confirmation = dialog(room.page, 'Revoke professional access')
    await action(room.page, 'POST', room.path + '/members/' + participant.member.grant_id + '/revoke', 200,
      button(confirmation, 'Confirm access removal'))
    await confirmation.waitFor({ state: 'hidden' })
    await request(professional, docs.firstPath + '/download', { expected: 404 })
    await request(professional, room.path, { expected: 404 })
    await request(buyer, docs.firstPath + '/download', { bytes: true })
    checks.push('unassigned_buyer_withheld_and_revoked_professional_denied')
    step = 'mobile_denied'
    await participant.page.bringToFront()
    const denied = pendingResponse(participant.page, 'GET', room.path)
    await participant.page.reload({ waitUntil: 'domcontentloaded' })
    await checkedResponse(denied, 404)
    await text(participant.page, 'This transaction or invitation is unavailable to your account.').waitFor()
    check(await button(participant.page, 'Download version 1').count() === 0, 'REVOKED_CONTROL_VISIBLE')
    checks.push('staff_only_document_audit_and_mobile_room')
    console.log('TRANSACTION_ROOM_BROWSER', JSON.stringify({ status: 'PASS', checks }))
  } finally {
    await browser.close()
  }
}

main().catch(error => {
  console.error('TRANSACTION_ROOM_BROWSER_FAILURE', step, failureReason(error))
  process.exitCode = 1
})
