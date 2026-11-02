const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const root = path.resolve(__dirname, '..')
function load(relative, overrides = {}) {
  const module = { exports: {} }
  const source = fs.readFileSync(path.join(root, relative), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText,
    { module, exports: module.exports, require: name => Object.hasOwn(overrides, name) ? overrides[name] : require(name) })
  return module.exports
}
const policy = load('apps/dashboard/src/lib/managementNavigation.ts')
const component = load('apps/dashboard/src/components/shared/ManagementNavigation.tsx', {
  '../../lib/managementNavigation': policy,
  'react-i18next': { useTranslation: () => ({ t: key => JSON.parse(fs.readFileSync(path.join(root, 'apps/dashboard/src/locale/en/nav.json'), 'utf8'))[key] }) },
})
test('actual rendered link requires both the real Admin account and Admin workspace', () => {
  for (const accountRole of ['buyer', 'owner', 'realtor', 'admin', 'Admin', '']) {
    for (const workspace of ['Buyer', 'Owner', 'Realtor', 'Admin', 'admin', '']) {
      const html = renderToStaticMarkup(React.createElement(component.ManagementNavigation, { accountRole, workspace }))
      if (accountRole === 'admin' && workspace === 'Admin') {
        assert.ok(html.includes('href="https://management.kaobahotel.com/"'))
        assert.ok(html.includes('Separate sign-in may be required'))
        assert.ok(html.includes('accounts and data remain separate'))
        assert.ok(html.includes('noopener noreferrer'))
        assert.ok(!/token=|email=|workspace=/.test(html))
      } else assert.equal(html, '', `${accountRole}/${workspace}`)
    }
  }
})
test('both locales describe navigation accurately and actual layout supplies real role separately', () => {
  for (const language of ['en', 'es']) {
    const locale = JSON.parse(fs.readFileSync(path.join(root, `apps/dashboard/src/locale/${language}/nav.json`), 'utf8'))
    assert.ok(locale.management_portal.trim())
    assert.ok(locale.management_navigation_note.trim())
  }
  const layout = fs.readFileSync(path.join(root, 'apps/dashboard/src/layouts/DashLayout.tsx'), 'utf8')
  assert.match(layout, /ManagementNavigation accountRole=\{user\.role\} workspace=\{role\}/)
  assert.equal(policy.managementNavigation('admin', 'Admin'), 'https://management.kaobahotel.com/')
})
