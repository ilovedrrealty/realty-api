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
    { module, exports: module.exports, URL, require: name => Object.hasOwn(overrides, name) ? overrides[name] : require(name) })
  return module.exports
}
const model = load('apps/dashboard/src/lib/contactShare.ts')
const labels = { name: 'Nombre', email: 'Correo', phone: 'Teléfono', listing: 'Anuncio' }
const contact = { name: 'Synthetic & Asociación', email: 'synthetic+villa@example.com', phone: '+18095550101',
  message: 'PRIVATE MESSAGE', notes: 'PRIVATE NOTES', tracking: { campaign: 'PRIVATE CAMPAIGN' },
  ghl_contact_url: 'PRIVATE CRM', property_address: 'PRIVATE ADDRESS', id: 'PRIVATE ID' }

test('minimum selection shares only the explicitly projected name, never the complete lead', () => {
  const preview = model.contactPreview(contact, model.minimalContactSelection(), labels)
  assert.equal(preview, 'Nombre: Synthetic & Asociación')
  for (const value of [contact.email, contact.phone, contact.message, contact.notes, contact.ghl_contact_url, contact.property_address, contact.id]) assert.ok(!preview.includes(value))
  assert.deepEqual(Object.keys(model.shareableContact(contact)).sort(), ['email', 'name', 'phone'])
  assert.notEqual(model.minimalContactSelection(), model.minimalContactSelection(), 'Selection must be fresh for each contact')
})

test('only checked valid contacts enter the exact preview; unsafe header data and invalid phones are excluded', () => {
  const selected = { name: false, email: true, phone: true, listing: false }
  assert.equal(model.contactPreview(contact, selected, labels), 'Correo: synthetic+villa@example.com\nTeléfono: +18095550101')
  for (const email of ['x@example.com\r\nBcc: secret@example.com', '<img>@example.com', 'x@example.com?bcc=secret@example.com', 'x@example.com\u0000', 'invalid']) {
    assert.equal(model.shareableContact({ name: 'A', email }).email, '')
  }
  for (const phone of ['+1', 'not a phone', '8095550101', '+18095550101\nBcc: a@b.co']) assert.equal(model.shareableContact({ name: 'A', phone }).phone, '')
  assert.equal(model.shareableContact({ name: 'A\nPhone: private' }).name, '')
})

test('email and WhatsApp links round-trip exactly, without recipient or header injection', () => {
  const preview = model.contactPreview(contact, { name: true, email: true, phone: false, listing: false }, labels)
  const links = model.contactComposeLinks(preview, 'Contacto: café & villa?')
  const email = new URL(links.email), whatsapp = new URL(links.whatsapp)
  assert.equal(email.pathname, '')
  assert.equal(email.searchParams.get('subject'), 'Contacto: café & villa?')
  assert.equal(email.searchParams.get('body'), preview)
  assert.deepEqual([...email.searchParams.keys()], ['subject', 'body'])
  assert.equal(whatsapp.origin, 'https://wa.me')
  assert.equal(whatsapp.searchParams.get('text'), preview)
  assert.deepEqual([...whatsapp.searchParams.keys()], ['text'])
  assert.equal(model.contactComposeLinks('', 'Contact'), null)
  assert.equal(model.contactComposeLinks('Contact', 'Contact\r\nBcc: private@example.com'), null)
})

test('future listing helper checks format only; private app URLs and extra tracking are rejected', () => {
  const link = 'https://ilovedrrealty.com/detail?id=00000000-0000-4000-8000-000000000001'
  assert.equal(model.publicListingShareUrl(link), link)
  const selected = { name: true, email: false, phone: false, listing: false }
  assert.ok(!model.contactPreview(contact, selected, labels, link).includes(link))
  assert.ok(model.contactPreview(contact, { ...selected, listing: true }, labels, link).endsWith('Anuncio: ' + link))
  for (const value of [link + '&token=private', link + '#private', 'https://app.ilovedrrealty.com/leads?openId=private', 'javascript:alert(1)', link.replace('ilovedrrealty.com', 'ilovedrrealty.com.evil.example'), link.replace('https:', 'http:')]) assert.equal(model.publicListingShareUrl(value), '')
})

test('actual React preview escapes hostile text and retains readable exact plain-text content', () => {
  const component = load('apps/dashboard/src/components/shared/ShareContactDialog.tsx', {
    '../../lib/contactShare': model,
    'react-i18next': { useTranslation: () => ({ t: key => key }) },
  })
  const text = '<img src=x onerror=alert(1)> & "quoted"\nCorreo: synthetic@example.com'
  const html = renderToStaticMarkup(React.createElement(component.ContactSharePreview, { text, empty: 'Empty' }))
  assert.ok(html.includes('&lt;img'))
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('&amp;'))
  assert.ok(html.includes('Correo: synthetic@example.com'))
  assert.ok(html.includes('whitespace-pre-wrap'))
})

test('actual integrations project three allowed fields and draft previews do not enable business sharing', () => {
  const lead = fs.readFileSync(path.join(root, 'apps/dashboard/src/components/admin/LeadDetailPanel.tsx'), 'utf8')
  const owner = fs.readFileSync(path.join(root, 'apps/dashboard/src/pages/owner/Leads.tsx'), 'utf8')
  const professional = fs.readFileSync(path.join(root, 'apps/dashboard/src/components/shared/ProfessionalPreview.tsx'), 'utf8')
  const directory = fs.readFileSync(path.join(root, 'apps/dashboard/src/pages/shared/ProfessionalDirectory.tsx'), 'utf8')
  for (const source of [lead, owner]) assert.match(source, /contact=\{\{ name: lead\.name, email: lead\.email, phone: lead\.phone \}\}/)
  assert.match(professional, /shareableBusiness = false/)
  assert.match(directory, /profile=\{detail\} options=\{options\} shareableBusiness/)
  const dialog = fs.readFileSync(path.join(root, 'apps/dashboard/src/components/shared/ShareContactDialog.tsx'), 'utf8')
  assert.ok(!/dangerouslySetInnerHTML|fetch\(|client\.(post|put)|localStorage/.test(dialog))
  assert.ok(!/publicListingShareUrl|type="url"|listingUrl/.test(dialog), 'Format-only URL utility must not imply a listing is publicly published')
})
