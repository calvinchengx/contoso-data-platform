// Serve the storefront's export from the materialised fixture files, ONE PAGE
// AT A TIME.
//
// Deliberately the same shape as contoso-pos/serve.js, and deliberately its
// own file. These are two vendors: sharing a handler would make one company's
// outage the other's, and would invite the "just add a feed" edit that quietly
// serves Contoso Web's bytes under Contoso POS's key. `make sources` writes
// each vendor's key into its own directory for the same reason.
//
// WHAT DIFFERS FROM POS, and it is only the format: this vendor ships JSON
// arrays rather than delimited text and JSON Lines. Each page is a complete
// array, so a client can parse a page without holding the export — which is
// what `paginate_json_array` in scripts/materialise_sources.py is for. A
// 30 MB single-line array served whole would cost ~10x that resident, which
// is the measurement that put paging here in the first place.
import { on } from 'mokapi'
import { read } from 'mokapi/file'

// The vendor's key, from the fixture that also produced the bytes.
// `make sources` writes it; nothing here restates it.
const KEY = read('/sources/_data/contoso-web/.api-key').trim()

const FEEDS = {
  exportWebCustomers: { dir: 'customers', ext: 'json', type: 'application/json' },
  exportWebProducts: { dir: 'products', ext: 'json', type: 'application/json' },
  exportWebOrders: { dir: 'orders', ext: 'json', type: 'application/json' },
}

const base = (dir) => `/sources/_data/contoso-web/${dir}`

// The page count is read from the directory, never restated here. A constant
// in this file would be a second source of truth for something `make sources`
// already decided, and the two would drift the first time the page size moved.
function totalPages(dir) {
  return parseInt(read(`${base(dir)}/pages.txt`).trim(), 10)
}

function pageFile(dir, ext, n) {
  return `${base(dir)}/page-${String(n).padStart(4, '0')}.${ext}`
}

export default function () {
  on('http', function (request, response) {
    const feed = FEEDS[request.operationId]
    if (!feed) return false

    // Checked BEFORE the page is read: an unauthenticated caller must not be
    // able to make the vendor do work. Contoso Web's key is its own — a POS
    // key must not open this door, which is what having two vendors means.
    if (request.header['X-Api-Key'] !== KEY) {
      response.statusCode = 401
      response.data = 'invalid api key'
      return true
    }

    const total = totalPages(feed.dir)
    const raw = request.query ? request.query['page'] : undefined
    const page = raw === undefined || raw === '' ? 1 : parseInt(raw, 10)

    // Out of range is a 404, not an empty 200. A client looping until it gets
    // nothing back cannot tell an empty page from a finished export, so the
    // vendor has to say which one it means.
    if (!Number.isInteger(page) || page < 1 || page > total) {
      response.statusCode = 404
      response.data = `no page ${raw} — this export has ${total}`
      return true
    }

    // A PLAIN LITERAL, not `response.headers[k] = v` and not a copy of the
    // existing object: mokapi's response.headers is a native binding, and
    // assigning it back to itself fails with `expected Object but got
    // *Object`. The handler then throws, mokapi falls back to SCHEMA
    // GENERATION, and the client gets 200 OK with invented bytes — a green
    // status carrying garbage.
    response.headers = {
      'X-Total-Pages': String(total),
      'X-Page': String(page),
      'Content-Type': feed.type,
    }
    // `body`, NOT `data`, and the difference is not cosmetic. `response.data`
    // is MARSHALLED against the operation's response schema — fine for POS,
    // whose schemas are `{type: string}`, but this vendor's responses are
    // declared as arrays of objects, so handing it pre-encoded JSON text fails
    // with `HTTP body marshalling failed` and a text/plain body. `response.body`
    // is the raw bytes, which is what serving a file means.
    //
    // Parsing the page here to satisfy the marshaller would work and would
    // undo the point: 9.5 MB of JSON becomes ~10x that as JS objects, which is
    // the cost paging exists to avoid.
    response.body = read(pageFile(feed.dir, feed.ext, page))
    return true
  })
}
