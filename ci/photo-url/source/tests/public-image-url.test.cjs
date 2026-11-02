'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const repoRoot = resolve(__dirname, '..')
const helperUrl = pathToFileURL(resolve(repoRoot, 'apps/landing/src/api/imgUrl.ts')).href
const objectUrl = 'https://example-project.supabase.co/storage/v1/object/public/listing-images/property/photo.jpg'

// Node24 imports the actual TypeScript module with native type stripping.
// No TypeScript compiler, replacement implementation, client or network is loaded.
async function imageUrl() {
  return (await import(helperUrl)).supabaseImgUrl
}

test('public listing photos retain their original object URL at each existing card width', async () => {
  const supabaseImgUrl = await imageUrl()
  for (const width of [400, 600, 800]) {
    assert.equal(supabaseImgUrl(objectUrl, width), objectUrl)
  }
})

test('query parameters, escaping and fragments are preserved exactly', async () => {
  const supabaseImgUrl = await imageUrl()
  for (const url of [
    objectUrl + '?download=Front%20view.jpg&version=2#photo',
    objectUrl + '?width=77&quality=91&name=a%2Fb%2Bc',
    'https://example-project.supabase.co/storage/v1/object/public/listing-images/Foto%20de%20jard%C3%ADn.jpg?x=1&x=2',
  ]) {
    assert.equal(supabaseImgUrl(url, 600, 82), url)
  }
})

test('private and signed routes are never converted into public image URLs', async () => {
  const supabaseImgUrl = await imageUrl()
  for (const url of [
    'https://example-project.supabase.co/storage/v1/object/authenticated/private/photo.webp',
    'https://example-project.supabase.co/storage/v1/object/sign/private/photo.webp?token=synthetic-test',
    '/property-network/media/10000000-0000-0000-0000-000000000001',
  ]) {
    assert.equal(supabaseImgUrl(url, 400), url)
  }
})

test('external, relative, blob, data and empty image values remain unchanged', async () => {
  const supabaseImgUrl = await imageUrl()
  for (const url of [
    'https://images.example.test/photo.jpg?fit=crop&w=400',
    'https://external.example.test/storage/v1/object/public/photos/a.jpg?original=1',
    '/images/property.jpg',
    'blob:https://example.test/synthetic-photo',
    'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==',
    '',
  ]) {
    assert.equal(supabaseImgUrl(url, 800, 90), url)
  }
})

test('an already stored transformation URL is preserved without implying zero transform traffic', async () => {
  const supabaseImgUrl = await imageUrl()
  const url = 'https://example-project.supabase.co/storage/v1/render/image/public/listing-images/property/photo.jpg?width=500&quality=70'
  assert.equal(supabaseImgUrl(url, 800), url)
})

test('the existing width and optional quality call signature stays compatible', async () => {
  const supabaseImgUrl = await imageUrl()
  assert.equal(supabaseImgUrl.length, 2)
  assert.equal(supabaseImgUrl(objectUrl, 600), objectUrl)
  assert.equal(supabaseImgUrl(objectUrl, 600, undefined), objectUrl)
  assert.equal(supabaseImgUrl(objectUrl, 600, 82), objectUrl)
})
