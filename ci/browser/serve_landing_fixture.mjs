import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, isAbsolute, join, relative, sep } from 'node:path'

const host = '127.0.0.1'
const port = 8917
const dist = '/landing-dist'
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

if (process.platform !== 'linux' || process.env.GITHUB_ACTIONS !== 'true' ||
    process.env.GITHUB_REPOSITORY !== 'ilovedrrealty/realty-api' ||
    await realpath(dist) !== dist || !(await stat(dist)).isDirectory()) {
  throw new Error('Exact hosted static landing directory required')
}

async function locate(pathname) {
  const pieces = pathname.split('/')
  if (!pathname.startsWith('/') || pathname.startsWith('//') || /[\\\x00-\x1f]/.test(pathname) ||
      pieces.some(piece => piece === '.' || piece === '..')) return null
  const requested = pathname.endsWith('/') ? pathname + 'index.html' : pathname
  try {
    const file = await realpath(join(dist, requested))
    const member = relative(dist, file)
    if (isAbsolute(member) || member === '..' || member.startsWith('..' + sep)) return null
    const info = await stat(file)
    return info.isFile() ? { file, info } : null
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null
    throw error
  }
}

const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store')
  response.setHeader('X-Content-Type-Options', 'nosniff')
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD' }).end()
    return
  }
  try {
    const pathname = decodeURIComponent((request.url ?? '').split('?', 1)[0])
    const selected = await locate(pathname)
    if (!selected) {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, {
      'Content-Type': contentTypes[extname(selected.file)] ?? 'application/octet-stream',
      'Content-Length': selected.info.size,
    })
    if (request.method === 'HEAD') response.end()
    else createReadStream(selected.file).on('error', () => response.destroy()).pipe(response)
  } catch (error) {
    if (response.headersSent) response.destroy()
    else response.writeHead(error instanceof URIError ? 400 : 500).end()
  }
})
server.listen(port, host)
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => server.close(() => process.exit(0)))
}
