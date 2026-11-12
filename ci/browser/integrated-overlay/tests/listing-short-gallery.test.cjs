'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { resolve } = require('node:path')
const { pathToFileURL } = require('node:url')

const gallerySource = pathToFileURL(resolve(__dirname, '../apps/landing/src/data/propertyDetailData.ts')).href
const photos = Array.from({ length: 8 }, (_, index) => `https://example.test/listing/${index + 1}.jpg`)

test('a listing with zero to four photos shows only real tiles in the original order', async () => {
  const { listingGallery } = await import(gallerySource)
  for (let count = 0; count <= 4; count += 1) {
    const available = photos.slice(0, count)
    const { photos: lightbox, tiles } = listingGallery(available)
    assert.deepEqual(lightbox, available)
    assert.deepEqual(tiles.map(tile => tile.src), available)
    assert.deepEqual(tiles.map(tile => tile.index), available.map((_, index) => index))
    assert.ok(tiles.every(tile => lightbox[tile.index] === tile.src && !tile.placement.includes('hidden') && tile.remainingCount === 0))
  }
})

test('empty image entries cannot create a clickable blank tile or move the lightbox out of range', async () => {
  const { listingGallery } = await import(gallerySource)
  const { photos: lightbox, tiles } = listingGallery(['', photos[0], '  ', photos[1]])
  assert.deepEqual(lightbox, photos.slice(0, 2))
  assert.deepEqual(tiles.map(tile => [tile.src, tile.index]), [[photos[0], 0], [photos[1], 1]])
})

test('five or more photos keep the original desktop and mobile tile positions and more badge', async () => {
  const { listingGallery } = await import(gallerySource)
  for (const count of [5, 8]) {
    const { photos: lightbox, tiles } = listingGallery(photos.slice(0, count))
    assert.deepEqual(tiles.map(tile => tile.index), [0, 1, 2, 3, 4])
    assert.deepEqual(tiles.filter(tile => !tile.placement.includes('hidden')).map(tile => tile.index), [0, 1, 4])
    assert.deepEqual(tiles.map(tile => tile.remainingCount), [0, 0, 0, 0, count - 4])
    assert.ok(tiles.every(tile => tile.src === lightbox[tile.index]))
  }
})
