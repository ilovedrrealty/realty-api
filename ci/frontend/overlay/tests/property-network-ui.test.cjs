const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const jsxRuntime = require('react/jsx-runtime')
const { renderToStaticMarkup } = require('react-dom/server')

const root = path.resolve(__dirname, '..')
const sourceRoot = 'apps/dashboard/src/'
const fixtureId = number => `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`
const ID = fixtureId(1), LISTING = fixtureId(2), PHOTO1 = fixtureId(3), PHOTO2 = fixtureId(4), ENTRY = fixtureId(5)
const page = items => ({ items, total: items.length, page: 1, page_size: 30 })
const author = { user_code: 71, display_name: 'Synthetic member' }
const profile = changes => ({ user_code: 71, display_name: 'Synthetic member', bio: 'Synthetic biography', status_text: '', visibility: 'private', wall_policy: 'own_only', comments_enabled: true, version: 0, can_edit: true, ...changes })
const project = changes => ({ id: ID, listing_id: LISTING, listing_title: 'Synthetic listing', title: 'Synthetic repair', summary: 'Synthetic project summary', property_class: 'house_villa', service_category_ids: ['air_conditioning'], status: 'open', version: 1, my_role: 'viewer', can_manage: false, can_assign: false, can_update: false, progress: { total: 0, completed: 0, percent: null }, created_at: '2026-01-01T12:00:00Z', updated_at: '2026-01-01T12:00:00Z', ...changes })

/**
 * Actual TSX + React's real JSX/SSR renderer. This bounded hook adapter stores
 * component-local state and runs effects against explicit mock API functions;
 * event handlers are the actual component handlers. It is not DOM/browser proof.
 * No production client, fetch, provider, storage URL or customer fixture is loaded.
 */
function ui(relative, exported, props, responses = {}, language = 'en') {
  const frames = new Map(), moduleCache = new Map(), componentTypes = new WeakMap(), effects = [], calls = [], revoked = [], listeners = new Map()
  const translations = JSON.parse(fs.readFileSync(path.join(root, sourceRoot, `locale/${language}/property_network.json`), 'utf8'))
  let current, seen, latestRoot, nextFunction = 0, nextUUID = 100, nextBlob = 0, html = '', dirty = false, allowLeave = true, confirmations = 0
  let clock = Date.parse('2026-01-02T12:00:00Z'), nextTimer = 0
  const timers = new Map(), fakeNavigator = { onLine: true }
  const lookup = (key, values = {}) => {
    let result = key.split('.').reduce((value, part) => value?.[part], translations)
    assert.equal(typeof result, 'string', `Missing ${language} translation: ${key}`)
    return result.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? ''))
  }
  const slot = kind => {
    assert.ok(current, 'Hooks must belong to an actual component render')
    const index = current.cursor++
    if (!current.slots[index]) current.slots[index] = { kind }
    assert.equal(current.slots[index].kind, kind, 'Hook order changed')
    return current.slots[index]
  }
  const hooks = { ...React,
    useState(initial) { const state = slot('state'); if (!Object.hasOwn(state, 'value')) state.value = typeof initial === 'function' ? initial() : initial; return [state.value, next => { state.value = typeof next === 'function' ? next(state.value) : next; dirty = true }] },
    useRef(initial) { const state = slot('ref'); if (!state.value) state.value = { current: initial }; return state.value },
    useEffect(effect, deps) { const state = slot('effect'); if (!state.deps || deps.some((value, index) => value !== state.deps[index])) { state.deps = [...deps]; effects.push({ state, effect }) } },
  }
  function resolveChildren(children, parentPath) {
    // Match the child-list boundary used by React: a top-level unkeyed Fragment
    // is transparent. Children.toArray retains nested-array and explicit-key scope.
    if (React.isValidElement(children) && children.type === React.Fragment && children.key === null) children = children.props.children
    return React.Children.toArray(children).map((value, index) => {
      if (!React.isValidElement(value)) return value
      const type = value.type
      let identity
      if (typeof type === 'function') {
        if (!componentTypes.has(type)) componentTypes.set(type, ++nextFunction)
        identity = ['component', componentTypes.get(type)]
      } else {
        assert.ok(typeof type === 'string' || type === React.Fragment, 'Unsupported element type in bounded mock renderer')
        identity = [type === React.Fragment ? 'fragment' : 'host', type === React.Fragment ? '' : type]
      }
      // Resolve after the parent tree exists, so keyed host ancestors (such as
      // timeline li elements) scope an unkeyed descendant's hook state too.
      const nodePath = [...parentPath, [identity, value.key ?? index]]
      if (typeof type !== 'function') {
        // Preserve absent/null children, especially on void input/img elements.
        if (value.props.children == null) return value
        return React.cloneElement(value, undefined, resolveChildren(value.props.children, nodePath))
      }
      const frameId = JSON.stringify(nodePath), previous = current
      assert.ok(!seen.has(frameId), 'Duplicate component identity in one mock render')
      if (!frames.has(frameId)) frames.set(frameId, { id: frameId, slots: [], cursor: 0 })
      current = frames.get(frameId); current.cursor = 0; seen.add(frameId)
      let tree
      try { tree = type(value.props) } finally { current = previous }
      return resolveChildren(tree, nodePath)
    })
  }
  const runtime = jsxRuntime
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args })
    assert.equal(typeof responses[name], 'function', `Unexpected API call: ${String(name)}`)
    return responses[name](...args)
  } })
  const fakeWindow = {
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback) },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback) },
    dispatchEvent(event) { for (const callback of listeners.get(event.type) ?? []) callback(event); return !event.defaultPrevented },
    confirm() { confirmations++; return allowLeave },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, due: clock + delay }); return id },
    clearTimeout(id) { timers.delete(id) },
  }
  const fakeDocument = { visibilityState: 'visible', addEventListener: fakeWindow.addEventListener, removeEventListener: fakeWindow.removeEventListener }
  function load(file) {
    const absolute = path.resolve(root, file)
    assert.ok(absolute.startsWith(root + path.sep), 'Only this worktree source may be loaded')
    if (moduleCache.has(absolute)) return moduleCache.get(absolute).exports
    const module = { exports: {} }; moduleCache.set(absolute, module)
    const source = fs.readFileSync(absolute, 'utf8')
    const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText
    vm.runInNewContext(compiled, { module, exports: module.exports, AbortController, Date: class extends Date { static now() { return clock } }, Intl, Blob, Event,
      document: fakeDocument, navigator: fakeNavigator,
      crypto: { randomUUID: () => fixtureId(++nextUUID) }, window: fakeWindow,
      URL: { createObjectURL: () => `blob:synthetic-${++nextBlob}`, revokeObjectURL: value => revoked.push(value) },
      require(name) {
        if (name === 'react') return hooks
        if (name === 'react/jsx-runtime') return runtime
        if (name === 'react-i18next') return { useTranslation: () => ({ t: lookup, i18n: { language } }) }
        if (name === 'axios') return { isAxiosError: error => error?.isAxiosError === true }
        if (name.endsWith('/api/propertyNetwork')) return api
        if (name.endsWith('/api/propertyManagement')) return { todayInDR: () => '2026-01-02' }
        assert.ok(name.startsWith('.'), `Unapproved module dependency: ${name}`)
        const base = path.resolve(path.dirname(absolute), name)
        const target = ['.tsx', '.ts', '/index.ts'].map(suffix => base + suffix).find(candidate => fs.existsSync(candidate))
        assert.ok(target, `Missing source module ${name}`)
        return load(path.relative(root, target))
      },
    }, { filename: absolute })
    return module.exports
  }
  const Component = load(sourceRoot + relative)[exported]
  assert.equal(typeof Component, 'function')
  function render() {
    dirty = false; seen = new Set(); latestRoot = resolveChildren(jsxRuntime.jsx(Component, props), [])
    html = renderToStaticMarkup(latestRoot)
    for (const [id, frame] of frames) if (!seen.has(id)) { for (const entry of frame.slots) entry.cleanup?.(); frames.delete(id) }
    return html
  }
  async function settle() {
    for (let attempt = 0; attempt < 15; attempt++) {
      render()
      const batch = effects.splice(0)
      for (const entry of batch) { entry.state.cleanup?.(); entry.state.cleanup = entry.effect() }
      for (let tick = 0; tick < 5; tick++) await Promise.resolve()
      if (!dirty && !effects.length) return html
    }
    assert.fail('UI did not settle within the bounded mock render loop')
  }
  function nodes(rootNode = latestRoot) {
    const result = []
    function visit(value) {
      if (Array.isArray(value)) { value.forEach(visit); return }
      if (!React.isValidElement(value)) return
      result.push(value); visit(value.props.children)
    }
    visit(rootNode); return result
  }
  function find(type, predicate = () => true) { const value = nodes().find(node => node.type === type && predicate(node)); assert.ok(value, `Missing rendered ${type}`); return value }
  const text = value => Array.isArray(value) ? value.map(text).join('') : React.isValidElement(value)
    ? text(value.props.children)
    : value == null || value === false ? '' : String(value)
  return { calls, revoked, t: lookup, render, settle, nodes, find, text, get html() { return html },
    get visibleText() { return text(latestRoot) },
    get confirmations() { return confirmations }, allowLeave(value) { allowLeave = value },
    async advance(amount) {
      const end = clock + amount
      for (let count = 0; count < 100; count++) {
        const next = [...timers.entries()].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0]
        if (!next) { clock = end; return settle() }
        clock = next[1].due; timers.delete(next[0]); next[1].callback(); await settle()
      }
      assert.fail('Unbounded mock timers')
    },
    async presence(visible, online) { fakeDocument.visibilityState = visible ? 'visible' : 'hidden'; fakeNavigator.onLine = online; fakeWindow.dispatchEvent(new Event('visibilitychange')); fakeWindow.dispatchEvent(new Event(online ? 'online' : 'offline')); return settle() },
    async fire(node, handler, event = {}) { node.props[handler](event); for (let tick = 0; tick < 5; tick++) await Promise.resolve(); return settle() },
    dispose() { for (const frame of frames.values()) for (const entry of frame.slots) entry.cleanup?.(); frames.clear(); effects.length = 0 },
  }
}

test('actual member editor starts private, creates nothing on read, and saves only deliberate visibility changes', async () => {
  let saved = profile()
  const h = ui('components/network/MemberCommunity.tsx', 'MemberProfile', { own: true }, {
    getMyNetworkProfile: async () => saved,
    saveNetworkProfile: async input => { saved = { ...saved, ...input, version: 1 }; return saved },
    getProfilePosts: async () => page([]),
  })
  try {
    await h.settle()
    assert.ok(h.html.includes(h.t('profile_private')))
    assert.ok(h.html.includes(h.t('save_profile_first')))
    assert.deepEqual(h.calls.map(call => call.name), ['getMyNetworkProfile'])
    const visibility = h.find('select', node => node.props.value === 'private')
    await h.fire(visibility, 'onChange', { target: { value: 'members' } })
    assert.equal(h.calls.filter(call => call.name === 'saveNetworkProfile').length, 0)
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    const write = h.calls.find(call => call.name === 'saveNetworkProfile').args[0]
    assert.equal(write.visibility, 'members'); assert.equal(write.expected_version, 0)
    for (const forbidden of ['email', 'phone', 'professional_profile_id', 'published_data', 'user_id']) assert.ok(!(forbidden in write))
    assert.ok(h.html.includes(h.t('profile_members')))
  } finally { h.dispose() }
})

test('actual project controls follow server capabilities rather than a selected workspace', async () => {
  for (const [role, capabilities] of [
    ['viewer', { can_manage: false, can_assign: false, can_update: false }],
    ['manager', { can_manage: true, can_assign: false, can_update: true }],
    ['owner', { can_manage: true, can_assign: true, can_update: true }],
  ]) {
    const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
      getProject: async () => project({ my_role: role, ...capabilities }), getProjectUpdates: async () => page([]),
      getProjectMembers: async () => [{ ...author, role: 'professional', active: true }],
    })
    try {
      await h.settle()
      assert.equal(h.html.includes(h.t('add_progress')), capabilities.can_update)
      assert.ok(h.html.includes(h.t('no_milestones')))
      assert.ok(!h.html.includes('0%'), 'No milestones is not measured zero progress')
      await h.fire(h.find('button', node => h.text(node) === h.t('tabs.members')), 'onClick')
      assert.equal(h.html.includes(h.t('add_member')), capabilities.can_assign)
      assert.equal(h.html.includes(h.t('revoke_access')), capabilities.can_assign)
      await h.fire(h.find('button', node => h.text(node) === h.t('tabs.details')), 'onClick')
      assert.equal(h.html.includes(h.t('save_changes')), capabilities.can_manage)
      assert.ok(h.calls.every(call => call.name.startsWith('get')), 'Rendering controls cannot write')
    } finally { h.dispose() }
  }
})

test('actual service history displays authorized work and server progress without leaking unrelated fixture fields', async () => {
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ progress: { total: 4, completed: 1, percent: 25 } }), getProjectUpdates: async () => page([]),
    getPropertyServiceHistory: async listingId => { assert.equal(listingId, LISTING); return page([{ id: ENTRY, project_title: 'Synthetic service', occurred_on: '2026-01-01', body: 'Synthetic work completed', media_ids: [], tenant_email: 'DO-NOT-RENDER@example.invalid', ledger: 'DO-NOT-RENDER-LEDGER', raw_snapshot: 'DO-NOT-RENDER-SNAPSHOT' }]) },
  })
  try {
    await h.settle(); assert.ok(h.html.includes('25%'))
    await h.fire(h.find('button', node => h.text(node) === h.t('tabs.history')), 'onClick')
    assert.ok(h.html.includes('Synthetic work completed')); assert.ok(h.html.includes(h.t('service_history_note')))
    assert.ok(!h.html.includes('DO-NOT-RENDER'))
    assert.ok(h.calls.every(call => !/statement|ledger|lease|tenant/i.test(String(call.name))))
  } finally { h.dispose() }
})

test('actual composer preserves exact pending submission on uncertainty and never reports message delivery', async () => {
  const writes = []; let successful = 0
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComposer', { dated: true, submit: async input => { writes.push(input); if (writes.length === 1) throw new Error('Synthetic unknown outcome'); return {} }, onSaved: () => successful++ })
  try {
    await h.settle()
    await h.fire(h.find('textarea'), 'onChange', { target: { value: '<script>synthetic & text</script>' } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(successful, 0); assert.equal(writes.length, 1)
    assert.ok(h.visibleText.includes(h.t('uncertain_submission'))); assert.ok(h.visibleText.includes(h.t('retry_same')))
    assert.equal(h.find('textarea').props.disabled, true)
    assert.ok(h.html.includes('&lt;script&gt;')); assert.ok(!h.html.includes('<script>'))
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(successful, 1); assert.equal(writes.length, 2)
    assert.deepEqual(JSON.parse(JSON.stringify(writes[1])), JSON.parse(JSON.stringify(writes[0])))
    assert.ok(!h.visibleText.includes(h.t('uncertain_submission')))
    assert.ok(!/message sent|delivered successfully|email sent/i.test(h.html))
  } finally { h.dispose() }
})

test('actual comments preserve parent-photo context, guard context changes and refuse foreign photo ids', async () => {
  const writes = []
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'update', canWrite: true, mediaIds: [PHOTO1, PHOTO2] }, {
    getUpdateComments: async () => page([{ id: fixtureId(8), body: 'Synthetic photo remark', media_id: PHOTO2, author, hidden: false, can_hide: false, created_at: '2026-01-01T12:00:00Z' }]),
    getNetworkMediaBlob: async () => new Blob(['synthetic'], { type: 'image/webp' }),
    createUpdateComment: async (id, input) => { assert.equal(id, ENTRY); writes.push(input); return {} },
  })
  try {
    await h.settle()
    await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
    assert.ok(h.html.includes(h.t('comment_photo', { number: 2 })))
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO1 } })
    await h.fire(h.find('textarea'), 'onChange', { target: { value: 'Synthetic draft for photo one' } })
    h.allowLeave(false)
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO2 } })
    assert.equal(h.find('select').props.value, PHOTO1); assert.ok(h.confirmations > 0)
    h.allowLeave(true)
    await h.fire(h.find('select'), 'onChange', { target: { value: PHOTO2 } })
    assert.equal(h.find('textarea').props.value, '', 'New context gets a new composer')
    await h.fire(h.find('select'), 'onChange', { target: { value: fixtureId(999) } })
    assert.equal(h.find('select').props.value, PHOTO2)
    await h.fire(h.find('textarea'), 'onChange', { target: { value: 'Synthetic comment for photo two' } })
    await h.fire(h.find('form'), 'onSubmit', { preventDefault() {} })
    assert.equal(writes.length, 1); assert.equal(writes[0].media_id, PHOTO2)
    assert.equal(writes[0].body, 'Synthetic comment for photo two')
    assert.ok(!('project_id' in writes[0]) && !('author_id' in writes[0]))
  } finally { h.dispose() }
})

test('actual private photo consumer uses authorized blob bytes and revokes its object URL', async () => {
  const h = ui('components/network/NetworkShared.tsx', 'PrivatePhotos', { ids: [PHOTO1] }, {
    getNetworkMediaBlob: async (id, signal) => { assert.equal(id, PHOTO1); assert.ok(signal); return new Blob(['synthetic'], { type: 'image/webp' }) },
  })
  await h.settle()
  assert.equal(h.calls.length, 1)
  assert.equal(h.find('img').props.src, 'blob:synthetic-1')
  assert.ok(h.find('img').props.className.includes('object-contain'))
  assert.ok(!/https?:\/\/|signed|storage\/v1|token=/.test(h.html))
  h.dispose(); assert.deepEqual(h.revoked, ['blob:synthetic-1'])
})

test('actual wall owner sees pending approval status and only a deliberate approve action changes it', async () => {
  let entry = { id: ENTRY, profile_user_code: 71, author, body: 'Synthetic submitted wall post', media_ids: [], status: 'pending', can_moderate: true, can_comment: false, created_at: '2026-01-01T12:00:00Z' }
  const h = ui('components/network/MemberCommunity.tsx', 'MemberProfile', { own: true }, {
    getMyNetworkProfile: async () => profile({ version: 1, visibility: 'members', wall_policy: 'approval_required' }),
    getProfilePosts: async () => page([entry]),
    moderatePost: async (id, action) => { assert.equal(id, ENTRY); assert.equal(action, 'approve'); entry = { ...entry, status: 'visible' }; return entry },
  })
  try {
    await h.settle()
    assert.ok(h.visibleText.includes(h.t('post_statuses.pending')))
    assert.equal(h.calls.filter(call => call.name === 'moderatePost').length, 0)
    await h.fire(h.find('button', node => h.text(node) === h.t('approve_post')), 'onClick')
    assert.equal(h.calls.filter(call => call.name === 'moderatePost').length, 1)
    assert.ok(h.visibleText.includes(h.t('post_statuses.visible')))
    assert.ok(!h.visibleText.includes(h.t('post_statuses.pending')))
  } finally { h.dispose() }
})

test('actual read-only comment consumer exposes no composer and Spanish UI resolves its actual copy', async () => {
  const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'post', canWrite: false, mediaIds: [] }, { getPostComments: async () => page([]) }, 'es')
  try {
    await h.settle(); await h.fire(h.find('button'), 'onClick')
    assert.ok(h.html.includes('Actualmente') || h.html.includes('actualmente'))
    assert.equal(h.nodes().filter(node => node.type === 'form').length, 0)
    assert.equal(h.nodes().filter(node => node.type === 'select').length, 0)
    assert.deepEqual(h.calls.map(call => call.name), ['getPostComments'])
  } finally { h.dispose() }
})

test('another participant timeline/comment and milestone ratio refresh without losing open drafts', async () => {
  const entry = { id: ENTRY, project_id: ID, kind: 'progress', body: 'Original work update', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' }
  let updates = [entry], comments = [], progress = { total: 2, completed: 0, percent: 0 }
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ progress, my_role: 'professional', can_update: true }), getProjectUpdates: async () => page(updates), getUpdateComments: async () => page(comments),
  })
  try {
    await h.settle()
    await h.fire(h.find('textarea', node => node.props.maxLength === 4000), 'onChange', { target: { value: 'Unsent project draft' } })
    await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
    await h.fire(h.find('textarea', node => node.props.maxLength === 2000), 'onChange', { target: { value: 'Unsent comment draft' } })
    updates = [{ ...entry, id: fixtureId(80), body: 'Another participant completed work' }, entry]
    comments = [{ id: fixtureId(81), body: 'Another participant comment', media_id: null, author, hidden: false, can_hide: false, created_at: '2026-01-02T12:00:20Z' }]
    progress = { total: 2, completed: 1, percent: 50 }
    await h.advance(30000)
    assert.ok(h.visibleText.includes('Another participant completed work'))
    assert.ok(h.visibleText.includes('Another participant comment'))
    assert.ok(h.visibleText.includes(h.t('progress', { completed: 1, total: 2, percent: '50' })))
    assert.equal(h.find('textarea', node => node.props.maxLength === 4000).props.value, 'Unsent project draft')
    assert.equal(h.find('textarea', node => node.props.maxLength === 2000).props.value, 'Unsent comment draft')
    assert.ok(h.calls.every(call => call.name.startsWith('get')))
  } finally { h.dispose() }
})

test('mounted English and Spanish comments show last-check/stale copy and pause offline or hidden', async () => {
  for (const language of ['en', 'es']) {
    let comments = []
    const h = ui('components/network/NetworkShared.tsx', 'NetworkComments', { recordId: ENTRY, kind: 'post', canWrite: false, mediaIds: [] }, { getPostComments: async () => page(comments) }, language)
    try {
      await h.settle(); assert.equal(h.calls.length, 0)
      await h.fire(h.find('button'), 'onClick')
      assert.ok(h.visibleText.includes(h.t('freshness_checked', { time: '' }).trim()))
      await h.presence(false, false); assert.ok(h.visibleText.includes(h.t('freshness_stale')))
      const count = h.calls.length; await h.advance(120000); assert.equal(h.calls.length, count)
      comments = [{ id: fixtureId(82), body: 'New permitted member comment', media_id: null, author, hidden: false, can_hide: false, created_at: '2026-01-02T12:00:20Z' }]
      await h.presence(true, true)
      assert.ok(h.visibleText.includes('New permitted member comment'))
      assert.ok(!h.visibleText.includes(h.t('freshness_stale')))
    } finally { h.dispose() }
  }
})

test('fresh permission denial clears project content and releases private photo object URLs', async () => {
  let denied = false
  const fail = () => { throw { isAxiosError: true, response: { status: 404 } } }
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => denied ? fail() : project(),
    getProjectUpdates: async () => denied ? fail() : page([{ id: ENTRY, project_id: ID, kind: 'progress', body: 'Private project work', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [PHOTO1], created_at: '2026-01-02T12:00:00Z' }]),
    getNetworkMediaBlob: async () => new Blob(['synthetic'], { type: 'image/webp' }),
  })
  try {
    await h.settle(); assert.equal(h.nodes().filter(node => node.type === 'img').length, 1)
    denied = true; await h.advance(30000)
    assert.ok(h.visibleText.includes(h.t('error_access')))
    assert.ok(!h.visibleText.includes('Private project work'))
    assert.equal(h.nodes().filter(node => node.type === 'img').length, 0)
    assert.deepEqual(h.revoked, ['blob:synthetic-1'])
  } finally { h.dispose() }
})

test('transient background timeline errors retain entries and unsent nested comment drafts', async () => {
  for (const failure of ['server', 'network']) {
    let failed = false
    const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
      getProject: async () => project({ my_role: 'professional', can_update: true }),
      getProjectUpdates: async () => {
        if (failed) {
          if (failure === 'server') throw { isAxiosError: true, response: { status: 500 } }
          throw new Error('Synthetic network failure')
        }
        return page([{ id: ENTRY, project_id: ID, kind: 'progress', body: 'Retained private timeline entry', occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' }])
      },
      getUpdateComments: async () => page([]),
    })
    try {
      await h.settle()
      await h.fire(h.find('button', node => h.text(node) === h.t('comments')), 'onClick')
      await h.fire(h.find('textarea', node => node.props.maxLength === 2000), 'onChange', { target: { value: 'Keep this unsent comment' } })
      failed = true; await h.advance(30000)
      assert.ok(h.nodes().some(node => node.props.role === 'alert' && h.text(node).includes(h.t('error_unknown'))))
      assert.ok(h.visibleText.includes('Retained private timeline entry'))
      assert.ok(h.visibleText.includes(h.t('freshness_stale')))
      assert.equal(h.find('textarea', node => node.props.maxLength === 2000).props.value, 'Keep this unsent comment')
      assert.ok(h.calls.every(call => call.name.startsWith('get')), 'No draft is submitted by refresh or failure')
    } finally { h.dispose() }
  }
})

test('separate keyed timeline entries keep independent comment drafts when reordered and another entry arrives', async () => {
  const entry = (id, body) => ({ id, project_id: ID, kind: 'progress', body, occurred_on: '2026-01-02', correction_of: null, author, media_ids: [], created_at: '2026-01-02T12:00:00Z' })
  const first = entry(ENTRY, 'First keyed entry'), second = entry(fixtureId(90), 'Second keyed entry')
  let entries = [first, second]
  const h = ui('components/network/ProjectWorkspace.tsx', 'ProjectDetail', { id: ID, onBack() {} }, {
    getProject: async () => project({ my_role: 'professional', can_update: true }),
    getProjectUpdates: async () => page(entries), getUpdateComments: async () => page([]),
  })
  const entryNode = body => h.find('li', node => h.text(node).includes(body))
  const commentsButton = body => h.nodes(entryNode(body)).find(node => node.type === 'button' && h.text(node) === h.t('comments'))
  const draft = body => h.nodes(entryNode(body)).find(node => node.type === 'textarea' && node.props.maxLength === 2000)
  try {
    await h.settle()
    await h.fire(commentsButton('First keyed entry'), 'onClick')
    await h.fire(draft('First keyed entry'), 'onChange', { target: { value: 'Draft only for first entry' } })
    assert.ok(commentsButton('Second keyed entry'), 'Opening the first thread must not open the second')
    assert.equal(draft('Second keyed entry'), undefined)
    await h.fire(commentsButton('Second keyed entry'), 'onClick')
    await h.fire(draft('Second keyed entry'), 'onChange', { target: { value: 'Different draft only for second entry' } })
    assert.equal(draft('First keyed entry').props.value, 'Draft only for first entry')
    assert.equal(draft('Second keyed entry').props.value, 'Different draft only for second entry')
    entries = [second, entry(fixtureId(91), 'New third keyed entry'), first]
    await h.advance(30000)
    assert.ok(h.visibleText.includes('New third keyed entry'))
    assert.equal(draft('First keyed entry').props.value, 'Draft only for first entry')
    assert.equal(draft('Second keyed entry').props.value, 'Different draft only for second entry')
    assert.equal(draft('New third keyed entry'), undefined)
    assert.ok(h.calls.every(call => call.name.startsWith('get')), 'Polling and reorder must not submit a draft')
  } finally { h.dispose() }
})
