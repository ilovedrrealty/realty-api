const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../apps/dashboard/src/api/propertyNetwork.ts'), 'utf8')
const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'
const C = '33333333-3333-4333-8333-333333333333'
const D = '44444444-4444-4444-8444-444444444444'
const NOW = '2026-01-01T12:00:00.123456+00:00'
const plain = value => JSON.parse(JSON.stringify(value))
const author = () => ({ user_code: 123, display_name: 'Synthetic member', email: 'private@example.com', id: D })
const property = () => ({ id: B, title: 'Synthetic villa', location: 'Synthetic town', type: 'villa', owner_email: 'private@example.com' })
const project = () => ({ id: A, listing_id: B, listing_title: 'Synthetic villa', title: 'AC service', summary: 'Synthetic project', property_class: 'house_villa', service_category_ids: ['air_conditioning'], status: 'open', version: 1, my_role: 'manager', can_manage: true, can_assign: false, can_update: true, progress: { total: 0, completed: 0, percent: null }, created_at: NOW, updated_at: NOW, owner_id: D, owner_email: 'private@example.com' })
const milestone = () => ({ id: C, project_id: A, title: 'Inspect AC', due_date: '2026-02-28', status: 'open', version: 1 })
const update = () => ({ id: C, project_id: A, kind: 'progress', body: 'Completed inspection', occurred_on: '2026-01-01', correction_of: null, author: author(), media_ids: [], created_at: NOW, uploader_id: D })
const comment = () => ({ id: D, body: 'Synthetic comment', media_id: null, author: author(), hidden: false, can_hide: false, created_at: NOW })
const profile = () => ({ user_code: 123, display_name: 'Synthetic member', bio: '', status_text: '', visibility: 'private', wall_policy: 'own_only', comments_enabled: true, version: 0, can_edit: true, email: 'private@example.com' })
const post = () => ({ id: C, profile_user_code: 123, author: author(), body: 'Synthetic wall post', media_ids: [], status: 'pending', can_moderate: true, can_comment: false, created_at: NOW })
const media = () => ({ id: D, url: `/property-network/media/${D}`, content_type: 'image/webp', bytes: 4, sha256: 'a'.repeat(64), width: 100, height: 100, object_key: 'private/object' })
const page = items => ({ items, total: items.length, page: 1, page_size: 30 })

function harness(initial) {
  const calls = [], exports = {}
  let response = { data: initial }, failure
  const invoke = async (method, url, ...args) => {
    calls.push({ method, url, args })
    if (failure) throw failure
    return response
  }
  const client = Object.fromEntries(['get', 'post', 'put'].map(method => [method, (url, ...args) => invoke(method, url, ...args)]))
  class TestFormData {
    constructor() { this.entries = [] }
    append(...entry) { this.entries.push(entry) }
  }
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, esModuleInterop: false } }).outputText
  vm.runInNewContext(compiled, { exports, Blob, FormData: TestFormData, Date, Intl, Set, Error, require: id => {
    assert.equal(id, './axios', 'Only the existing authenticated client may be imported')
    return { default: client }
  } })
  return { api: exports, calls, reply: data => { response = { data }; failure = undefined },
    fullReply: value => { response = value; failure = undefined }, fail: error => { failure = error } }
}

test('paged property reads use authenticated client, abort signals and safe projections', async () => {
  const h = harness(page([property()])), signal = { synthetic: true }
  const result = await h.api.getNetworkProperties({ page: 1, owner_id: D }, signal)
  assert.deepEqual(plain(result.items[0]), { id: B, title: 'Synthetic villa', location: 'Synthetic town', type: 'villa' })
  assert.equal(h.calls[0].url, '/property-network/properties')
  assert.deepEqual(plain(h.calls[0].args[0].params), { page: 1 })
  assert.equal(h.calls[0].args[0].signal, signal)
  assert.equal(h.calls[0].args[0].withCredentials, true)
})

test('malformed, duplicate or mismatched pages fail instead of appearing empty', async () => {
  for (const response of [null, {}, { ...page([]), items: null }, { ...page([]), page: 2 },
    { ...page([]), page_size: 31 }, { ...page([]), total: -1 }, page([property(), property()]),
    { ...page([property()]), total: 0 }, page(Array.from({ length: 31 }, property))]) {
    const h = harness(response)
    await assert.rejects(h.api.getNetworkProperties(), /Invalid property network data/)
  }
})

test('project views discard private extras and reject invalid capabilities, identifiers and progress', async () => {
  const h = harness(project())
  const row = await h.api.getProject(A)
  assert.equal(Object.hasOwn(row, 'owner_email'), false)
  assert.equal(Object.hasOwn(row, 'owner_id'), false)
  for (const changes of [{ id: B }, { can_assign: 'false' }, { status: 'published' },
    { progress: { total: 1, completed: 2, percent: 100 } }, { created_at: '2026-02-30T00:00:00Z' }]) {
    h.reply({ ...project(), ...changes })
    await assert.rejects(h.api.getProject(A), /Invalid property network data/)
  }
})

test('invalid path identifiers and page bounds are refused before contacting the client', async () => {
  const h = harness(page([]))
  await assert.rejects(h.api.getProject('../private'), /Invalid property network data/)
  await assert.rejects(h.api.getProjects({ page: 0 }), /Invalid property network data/)
  await assert.rejects(h.api.getNetworkProfiles({ page: 1001 }), /Invalid property network data/)
  assert.throws(() => h.api.getMilestones('https://untrusted.example'), /Invalid property network data/)
  assert.equal(h.calls.length, 0)
})

test('project writes send only declared fields and preserve version conflicts as failures', async () => {
  const h = harness(project())
  await h.api.createProject({ listing_id: B, title: 'AC service', summary: 'Synthetic project', property_class: 'house_villa', service_category_ids: ['air_conditioning'], owner_id: D, can_assign: true })
  assert.deepEqual(Object.keys(h.calls[0].args[0]).sort(), ['listing_id', 'property_class', 'service_category_ids', 'summary', 'title'])
  const conflict = new Error('synthetic conflict')
  h.fail(conflict)
  await assert.rejects(h.api.updateProject(A, { expected_version: 1, title: 'AC service', summary: '', status: 'in_progress' }), error => error === conflict)
  assert.equal(h.calls.length, 2, 'No automatic retry is performed')
})

test('member reads and assignment mutations preserve the real array contract and hide contact fields', async () => {
  const members = [{ user_code: 123, display_name: 'Synthetic owner', role: 'owner', active: true, email: 'private@example.com' }]
  const h = harness(members)
  assert.deepEqual(plain(await h.api.getProjectMembers(A)), [{ user_code: 123, display_name: 'Synthetic owner', role: 'owner', active: true }])
  await h.api.addProjectMember(A, { expected_version: 1, user_code: 456, role: 'professional' })
  assert.deepEqual(plain(h.calls[1].args[0]), { expected_version: 1, user_code: 456, role: 'professional' })
  await h.api.revokeProjectMember(A, 456, 2)
  assert.equal(h.calls[2].url, `/property-network/projects/${A}/members/456/revoke`)
  assert.deepEqual(plain(h.calls[2].args[0]), { expected_version: 2 })
  await assert.rejects(h.api.addProjectMember(A, { expected_version: 1, user_code: 456, role: 'admin' }), /Invalid property network data/)
  assert.equal(h.calls.length, 3)
})

test('milestones enforce real calendar dates, versions and same-project response identity', async () => {
  const h = harness(milestone())
  await h.api.createMilestone(A, { title: 'Inspect AC', due_date: '2026-02-28' })
  await assert.rejects(h.api.createMilestone(A, { title: 'Invalid date', due_date: '2026-02-30' }), /Invalid property network data/)
  assert.equal(h.calls.length, 1)
  h.reply({ ...milestone(), project_id: B })
  await assert.rejects(h.api.createMilestone(A, { title: 'Inspect AC', due_date: null }), /Invalid property network data/)
  h.reply(milestone())
  await h.api.updateMilestone(C, { title: 'Inspect AC', due_date: null, expected_version: 1, status: 'completed' })
  assert.equal(h.calls.at(-1).args[0].expected_version, 1)
})

test('progress submissions retain the supplied id after an uncertain attempt and never retry automatically', async () => {
  const h = harness(update()), input = { submission_id: D, body: 'Completed inspection', occurred_on: '2026-01-01', correction_of: null, media_ids: [] }
  h.fail(new Error('synthetic connection failure'))
  await assert.rejects(h.api.createProjectUpdate(A, input), /synthetic connection failure/)
  assert.equal(h.calls.length, 1)
  h.reply(update())
  const result = await h.api.createProjectUpdate(A, input)
  assert.equal(h.calls[0].args[0].submission_id, D)
  assert.equal(h.calls[1].args[0].submission_id, D)
  assert.equal(result.author.user_code, 123)
  assert.equal(Object.hasOwn(result.author, 'email'), false)
  assert.equal(Object.hasOwn(result, 'uploader_id'), false)
  await assert.rejects(h.api.createProjectUpdate(A, { ...input, occurred_on: '2999-01-01' }), /Invalid property network data/)
  await assert.rejects(h.api.createProjectUpdate(A, { ...input, media_ids: [C, C] }), /Invalid property network data/)
  assert.equal(h.calls.length, 2)
})

test('comments and wall posts project only safe fields and preserve submission ids', async () => {
  const h = harness({ ...comment(), author: { user_code: null, display_name: 'Former member', email: 'private@example.com' } })
  const result = await h.api.createUpdateComment(C, { submission_id: A, body: 'Synthetic comment', author_id: B })
  assert.deepEqual(plain(result.author), { user_code: null, display_name: 'Former member' })
  assert.deepEqual(plain(h.calls[0].args[0]), { submission_id: A, body: 'Synthetic comment' })
  h.reply(post())
  await h.api.createProfilePost(123, { submission_id: A, body: 'Synthetic wall post', media_ids: [] })
  assert.equal(h.calls[1].args[0].submission_id, A)
  assert.equal(Object.hasOwn(await h.api.moderatePost(C, 'approve'), 'author_id'), false)
  h.reply(comment())
  await h.api.createPostComment(C, { submission_id: B, body: 'Synthetic comment' })
  await h.api.hideNetworkComment(D)
  assert.deepEqual(plain(h.calls.at(-1).args[0]), {})
})

test('profile defaults and saving remain explicit about privacy, comments and optimistic version', async () => {
  const h = harness(profile())
  const own = await h.api.getMyNetworkProfile()
  assert.equal(own.version, 0)
  assert.equal(own.visibility, 'private')
  assert.equal(Object.hasOwn(own, 'email'), false)
  await h.api.saveNetworkProfile({ expected_version: 0, display_name: 'Synthetic member', bio: '', status_text: '', visibility: 'private', wall_policy: 'own_only', comments_enabled: false, user_id: A })
  assert.equal(h.calls[1].args[0].comments_enabled, false)
  assert.equal(Object.hasOwn(h.calls[1].args[0], 'user_id'), false)
  await assert.rejects(h.api.saveNetworkProfile({ expected_version: 0, display_name: 'Synthetic member', bio: '', status_text: '', visibility: 'public', wall_policy: 'own_only', comments_enabled: true }), /Invalid property network data/)
  h.reply({ blocked: true, email: 'private@example.com' })
  assert.deepEqual(plain(await h.api.setNetworkBlock(456, true)), { blocked: true })
  h.reply({ blocked: 'true' })
  await assert.rejects(h.api.setNetworkBlock(456, true), /Invalid property network data/)
})

test('per-photo comments retain the supplied media context without changing whole-post payloads', async () => {
  const h = harness({ ...comment(), media_id: C })
  const result = await h.api.createPostComment(B, { submission_id: A, body: 'Synthetic photo comment', media_id: C })
  assert.equal(result.media_id, C)
  assert.deepEqual(plain(h.calls[0].args[0]), { submission_id: A, body: 'Synthetic photo comment', media_id: C })
  h.reply(comment())
  await h.api.createPostComment(B, { submission_id: D, body: 'Synthetic whole-post comment' })
  assert.equal(Object.hasOwn(h.calls[1].args[0], 'media_id'), false)
  await assert.rejects(h.api.createPostComment(B, { submission_id: A, body: 'Synthetic', media_id: '../other-photo' }), /Invalid property network data/)
  assert.equal(h.calls.length, 2)
})

test('service history retains event identity but discards author and property-management data', async () => {
  const row = { id: C, project_id: A, project_title: 'AC service', service_category_ids: ['air_conditioning'], project_status: 'completed', kind: 'progress', body: 'Completed inspection', occurred_on: '2026-01-01', media_ids: [], created_at: NOW, author: author(), lease: { tenant_email: 'private@example.com' } }
  const h = harness(page([row]))
  const result = await h.api.getPropertyServiceHistory(B)
  assert.equal(result.items[0].id, C)
  assert.equal(Object.hasOwn(result.items[0], 'author'), false)
  assert.equal(Object.hasOwn(result.items[0], 'lease'), false)
})

test('photo uploads remain private multipart requests with bounded files and canonical response identity', async () => {
  const h = harness(media()), file = new Blob(['test'], { type: 'image/png' }), signal = { synthetic: true }
  const result = await h.api.uploadNetworkMedia(file, A, signal)
  assert.equal(result.id, D)
  assert.equal(Object.hasOwn(result, 'object_key'), false)
  assert.equal(h.calls[0].url, '/property-network/media')
  assert.deepEqual(h.calls[0].args[0].entries.map(entry => entry[0]), ['file', 'project_id'])
  assert.equal(h.calls[0].args[0].entries[1][1], A)
  assert.equal(h.calls[0].args[1].withCredentials, true)
  assert.equal(h.calls[0].args[1].signal, signal)
  await assert.rejects(h.api.uploadNetworkMedia(new Blob(['bad'], { type: 'text/html' })), /Invalid property network data/)
  h.reply({ ...media(), url: 'https://untrusted.example/public-image' })
  await assert.rejects(h.api.uploadNetworkMedia(file), /Invalid property network data/)
})

test('private photos use authenticated no-store blob reads without public or signed URLs', async () => {
  const h = harness(null), blob = new Blob(['webp'], { type: 'image/webp' }), signal = { synthetic: true }
  h.fullReply({ data: blob, headers: { 'cache-control': 'no-store' } })
  assert.equal(await h.api.getNetworkMediaBlob(D, signal), blob)
  assert.equal(h.calls[0].url, `/property-network/media/${D}`)
  assert.equal(h.calls[0].args[0].responseType, 'blob')
  assert.equal(h.calls[0].args[0].withCredentials, true)
  assert.equal(h.calls[0].args[0].signal, signal)
  for (const response of [{ data: blob, headers: {} }, { data: new Blob(['bad'], { type: 'text/html' }), headers: { 'cache-control': 'no-store' } }, { data: 'https://untrusted.example/signed', headers: { 'cache-control': 'no-store' } }]) {
    h.fullReply(response)
    await assert.rejects(h.api.getNetworkMediaBlob(D), /Invalid property network data/)
  }
})

test('all paged conversation feeds use fixed paging and reject wrong parent/profile rows', async () => {
  const h = harness(page([update()]))
  await h.api.getProjectUpdates(A)
  h.reply(page([{ ...update(), project_id: B }]))
  await assert.rejects(h.api.getProjectUpdates(A), /Invalid property network data/)
  h.reply(page([milestone()])); await h.api.getMilestones(A)
  h.reply(page([comment()])); await h.api.getUpdateComments(C); await h.api.getPostComments(C)
  h.reply(page([profile()])); await h.api.getNetworkProfiles({ q: 'Synthetic', page: 1 })
  h.reply(profile()); await h.api.getNetworkProfile(123)
  h.reply(page([post()])); await h.api.getProfilePosts(123)
  h.reply(page([{ ...post(), profile_user_code: 456 }]))
  await assert.rejects(h.api.getProfilePosts(123), /Invalid property network data/)
})

const registryRecord = (changes = {}) => ({ id: B, title: 'Private synthetic property', location: 'Synthetic town', address: 'Private synthetic address', property_class: 'house_villa', relationship: 'manager', notes: 'Private synthetic notes', status: 'active', version: 1, created_at: NOW, updated_at: NOW, can_manage: true, can_create_project: true, ...changes })
const registryInput = () => ({ submission_id: D, title: 'Private synthetic property', location: 'Synthetic town', address: '', property_class: 'house_villa', relationship: 'manager', notes: '', authority_acknowledged: true })
const registryProject = () => ({ ...project(), listing_id: null, property_id: B, property_title: 'Private synthetic property', listing_title: 'Private synthetic property', address: 'must not project', location: 'must not project', notes: 'must not project', relationship: 'manager', custodian_id: D })

test('private registry pages use only current-actor filters and strip custodian and account extras', async () => {
  const h = harness({ ...page([registryRecord({ custodian_id: D, email: 'private@example.com' })]), can_create: true })
  const signal = { synthetic: true }
  const result = await h.api.getPrivateProperties({ page: 1, status: 'active', custodian_id: D }, signal)
  assert.equal(result.can_create, true)
  assert.deepEqual(plain(h.calls[0].args[0]), { params: { page: 1, status: 'active' }, signal, withCredentials: true })
  assert.equal(h.calls[0].url, '/property-network/registry')
  assert.equal(result.items[0].address, 'Private synthetic address', 'Full own-record fields are distinct from a project summary')
  for (const key of ['custodian_id', 'email', 'submission_id']) assert.equal(Object.hasOwn(result.items[0], key), false)
  for (const response of [{ ...page([]), can_create: 'true' }, { ...page([registryRecord(), registryRecord()]), can_create: true }, { ...page([registryRecord({ status: 'archived', can_create_project: false })]), can_create: true }]) {
    h.reply(response)
    await assert.rejects(h.api.getPrivateProperties({ status: 'active' }), /Invalid property network data/)
  }
})

test('private registration requires explicit authority and the real bounded property fields before a write', async () => {
  const h = harness(registryRecord())
  for (const changes of [{ authority_acknowledged: false }, { authority_acknowledged: 'true' }, { title: ' ' }, { location: '' }, { address: 'x'.repeat(501) }, { notes: 'x'.repeat(4001) }, { property_class: 'published_listing' }, { relationship: 'admin' }, { submission_id: '../foreign' }]) {
    await assert.rejects(h.api.createPrivateProperty({ ...registryInput(), ...changes }), /Invalid property network data/)
  }
  assert.equal(h.calls.length, 0)
  await h.api.createPrivateProperty({ ...registryInput(), custodian_id: A, owner_id: A, is_public: true })
  assert.deepEqual(Object.keys(h.calls[0].args[0]).sort(), ['address', 'authority_acknowledged', 'location', 'notes', 'property_class', 'relationship', 'submission_id', 'title'])
  assert.equal(h.calls[0].args[0].authority_acknowledged, true)
  assert.equal(h.calls[0].args[0].submission_id, D)
})

test('uncertain property creates retain an explicit same request and both resolvers are authenticated reads', async () => {
  const h = harness(registryRecord()), input = registryInput(), failure = new Error('synthetic timeout')
  h.fail(failure)
  await assert.rejects(h.api.createPrivateProperty(input), error => error === failure)
  assert.equal(h.calls.length, 1)
  h.reply(registryRecord({ title: 'Later edited property', version: 2 }))
  await h.api.getPrivatePropertySubmission(D)
  assert.equal(h.calls[1].method, 'get')
  assert.equal(h.calls[1].url, `/property-network/registry/submissions/${D}`)
  await h.api.createPrivateProperty(input)
  assert.deepEqual(plain(h.calls[2].args[0]), plain(h.calls[0].args[0]), 'Only a later explicit call repeats the immutable original payload')
  h.reply(registryProject())
  await h.api.getProjectSubmission(D)
  assert.equal(h.calls[3].url, `/property-network/projects/submissions/${D}`)
  assert.equal(h.calls[3].method, 'get')
  assert.equal(h.calls[3].args[0].withCredentials, true)
})

test('property edits and archive preserve version failures and reject contradictory project capabilities', async () => {
  const h = harness(registryRecord({ status: 'archived', version: 2, can_create_project: false }))
  const { submission_id, authority_acknowledged, ...values } = registryInput()
  await h.api.updatePrivateProperty(B, { ...values, expected_version: 1, status: 'archived', custodian_id: A })
  assert.equal(h.calls[0].method, 'put')
  assert.equal(h.calls[0].args[0].expected_version, 1)
  assert.equal(h.calls[0].args[0].status, 'archived')
  assert.ok(!Object.hasOwn(h.calls[0].args[0], 'custodian_id'))
  const conflict = { isAxiosError: true, response: { status: 409 } }
  h.fail(conflict)
  await assert.rejects(h.api.updatePrivateProperty(B, { ...values, expected_version: 1, status: 'active' }), error => error === conflict)
  assert.equal(h.calls.length, 2, 'No automatic conflict retry')
  h.reply(registryRecord({ status: 'archived', can_create_project: true }))
  await assert.rejects(h.api.getPrivateProperty(B), /Invalid property network data/)
})

test('project source is exactly one listing or private property with mandatory private submission reference', async () => {
  const h = harness(registryProject()), base = { title: 'AC service', summary: '', property_class: 'house_villa', service_category_ids: ['air_conditioning'] }
  for (const input of [{ ...base }, { ...base, listing_id: A, property_id: B, submission_id: D }, { ...base, property_id: B }]) await assert.rejects(h.api.createProject(input), /Invalid property network data/)
  assert.equal(h.calls.length, 0)
  const result = await h.api.createProject({ ...base, property_id: B, submission_id: D, custodian_id: A })
  assert.equal(result.listing_id, null)
  assert.equal(result.property_id, B)
  assert.equal(result.property_title, 'Private synthetic property')
  for (const key of ['address', 'location', 'notes', 'relationship', 'custodian_id']) assert.equal(Object.hasOwn(result, key), false)
  assert.equal(Object.hasOwn(h.calls[0].args[0], 'listing_id'), false)
  assert.equal(h.calls[0].args[0].property_id, B)
  assert.equal(h.calls[0].args[0].submission_id, D)
  h.reply({ ...registryProject(), listing_id: A })
  await assert.rejects(h.api.getProject(A), /Invalid property network data/)
  assert.throws(() => h.api.getProjects({ listing_id: A, property_id: B }), /Invalid property network data/)
})

test('private property project filters and service history never fetch registry detail or project private fields', async () => {
  const item = { id: C, project_id: A, project_title: 'AC service', service_category_ids: ['air_conditioning'], project_status: 'open', kind: 'progress', body: 'Authorized service entry', occurred_on: '2026-01-01', media_ids: [], created_at: NOW, address: 'do not expose', notes: 'do not expose' }
  const h = harness(page([item]))
  const history = await h.api.getPrivatePropertyServiceHistory(B)
  assert.equal(h.calls[0].url, `/property-network/registry/${B}/history`)
  assert.equal(history.items[0].body, 'Authorized service entry')
  assert.equal(Object.hasOwn(history.items[0], 'address'), false)
  assert.equal(Object.hasOwn(history.items[0], 'notes'), false)
  h.reply(page([registryProject()]))
  await h.api.getProjects({ property_id: B })
  assert.deepEqual(plain(h.calls[1].args[0].params), { page: 1, property_id: B })
  assert.ok(h.calls.every(call => call.url !== `/property-network/registry/${B}`))
})
