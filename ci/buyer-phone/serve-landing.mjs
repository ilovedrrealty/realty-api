import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, isAbsolute, join, relative, sep } from 'node:path'

if (process.platform !== 'linux' || process.env.GITHUB_ACTIONS !== 'true'
    || process.env.GITHUB_REPOSITORY !== 'ilovedrrealty/realty-api') throw new Error('CI runner required')
const root = '/landing-out'
if (await realpath(root) !== root || !(await stat(root)).isDirectory()) throw new Error('Owned export required')
const types = new Map([['.html', 'text/html; charset=utf-8'], ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'], ['.json', 'application/json'], ['.png', 'image/png'], ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'], ['.svg', 'image/svg+xml'], ['.webp', 'image/webp'], ['.ico', 'image/x-icon'],
  ['.woff', 'font/woff'], ['.woff2', 'font/woff2']])
async function member(name) {
  try {
    const file = await realpath(join(root, name)), relation = relative(root, file)
    if (isAbsolute(relation) || relation === '..' || relation.startsWith('..' + sep)) return null
    const info = await stat(file)
    return info.isFile() ? { file, info } : null
  } catch (cause) {
    if (cause.code === 'ENOENT' || cause.code === 'ENOTDIR') return null
    throw cause
  }
}
if (!await member('signup/index.html')) throw new Error('Actual Signup export required')
const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store'); response.setHeader('X-Content-Type-Options', 'nosniff')
  if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405, { Allow: 'GET, HEAD' }).end(); return }
  try {
    const pathname = decodeURIComponent((request.url ?? '').split('?', 1)[0]), pieces = pathname.split('/')
    if (!pathname.startsWith('/') || pathname.startsWith('//') || pathname.includes('\\') || pathname.includes('\0')
        || pieces.includes('.') || pieces.includes('..')) { response.writeHead(400).end(); return }
    const name = pieces.slice(1).join('/')
    const found = await member(name) ?? await member(name + (name.endsWith('/') || !name ? '' : '/') + 'index.html')
    if (!found) { response.writeHead(404).end(); return }
    response.writeHead(200, { 'Content-Type': types.get(extname(found.file)) ?? 'application/octet-stream', 'Content-Length': found.info.size })
    if (request.method === 'HEAD') response.end()
    else createReadStream(found.file).on('error', () => response.destroy()).pipe(response)
  } catch (cause) {
    if (response.headersSent) response.destroy()
    else response.writeHead(cause instanceof URIError ? 400 : 500).end()
  }
})
server.listen(8917, '127.0.0.1')
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.close(() => process.exit(0)))
