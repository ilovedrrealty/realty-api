import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, isAbsolute, join, relative, sep } from 'node:path'

const HOST = '127.0.0.1'
const PORT = 4175
const TYPES = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.gif', 'image/gif'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.jpeg', 'image/jpeg'],
  ['.jpg', 'image/jpeg'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webp', 'image/webp'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
])

if (process.platform !== 'linux' || process.env.GITHUB_ACTIONS !== 'true' ||
    process.env.GITHUB_REPOSITORY !== 'ilovedrrealty/realty-api') {
  throw new Error('CI runner required')
}
const dist = process.env.ILDR_BROWSER_DASHBOARD_DIST ?? '/dashboard-dist'
if (!isAbsolute(dist) || await realpath(dist) !== dist || !(await stat(dist)).isDirectory()) {
  throw new Error('Absolute dashboard build directory required')
}

async function builtFile(name) {
  try {
    const file = await realpath(join(dist, name))
    const member = relative(dist, file)
    if (isAbsolute(member) || member === '..' || member.startsWith(`..${sep}`)) return null
    const info = await stat(file)
    return info.isFile() ? { file, info } : null
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null
    throw error
  }
}

const index = await builtFile('index.html')
if (!index) throw new Error('Dashboard build is missing index.html')

const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store')
  response.setHeader('X-Content-Type-Options', 'nosniff')
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD' }).end()
    return
  }

  try {
    const pathname = decodeURIComponent((request.url ?? '').split('?', 1)[0])
    const components = pathname.split('/')
    if (!pathname.startsWith('/') || pathname.startsWith('//') || pathname.includes('\\') ||
        pathname.includes('\0') || components.includes('.') || components.includes('..')) {
      response.writeHead(400).end()
      return
    }
    const direct = await builtFile(components.slice(1).join('/'))
    const isDashboardRoute = /^\/[a-z0-9-]*\/?$/.test(pathname)
    const selected = direct ?? (isDashboardRoute ? index : null)
    if (!selected) {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, {
      'Content-Type': TYPES.get(extname(selected.file)) ?? 'application/octet-stream',
      'Content-Length': selected.info.size,
    })
    if (request.method === 'HEAD') response.end()
    else createReadStream(selected.file).on('error', () => response.destroy()).pipe(response)
  } catch (error) {
    if (response.headersSent) response.destroy()
    else response.writeHead(error instanceof URIError ? 400 : 500).end()
  }
})

server.listen(PORT, HOST)
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => server.close(() => process.exit(0)))
}
