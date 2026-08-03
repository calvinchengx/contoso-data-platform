// Serve the vendor's export from the materialised fixture files, ONE PAGE AT A
// TIME.
//
// WHY FILES AND NOT mokapi's SCHEMA GENERATION: generation is random per
// request and random in shape — optional properties are dropped per row — so a
// generated body cannot back `assert n_orders == 247_500`. The bytes here come
// from the same seeded generator the fabric-emulator examples assert against,
// which is the entire reason the two repositories agree on their numbers.
//
// WHY THE PAGES ARE SEPARATE FILES: `read()` returns a whole file — mokapi has
// no seek and no range — so slicing here would cost exactly what returning the
// whole export costs. Measured: a 95 MB body cost 944 MB of container memory,
// 10.4x, and seven of them killed the process mid-response. The split happens
// in `make sources`; this reads one page and holds one page.
import { on } from 'mokapi'
import { read } from 'mokapi/file'

// The vendor's key, from the fixture that also produced the bytes.
// `make sources` writes it; nothing here restates it.
const KEY = read('/sources/_data/contoso-pos/.api-key').trim()

const FEEDS = {
  exportCustomers: { dir: 'customers', ext: 'csv', type: 'text/csv' },
  exportOrders: { dir: 'orders', ext: 'jsonl', type: 'application/x-ndjson' },
}

const base = (dir) => `/sources/_data/contoso-pos/${dir}`

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

    // A real 401, replacing the in-process PermissionError the emulator's own
    // example raises. The extract step asserts a wrong key is refused, and it
    // should be refused the way an HTTP client would actually experience it.
    // Checked BEFORE the page is read: an unauthenticated caller must not be
    // able to make the vendor do work.
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
    // *Object`. That error is worth knowing because of how it surfaces — the
    // handler throws, mokapi falls back to SCHEMA GENERATION, and the client
    // gets 200 OK with invented bytes. A green status carrying garbage.
    response.headers = {
      'X-Total-Pages': String(total),
      'X-Page': String(page),
      'Content-Type': feed.type,
    }
    response.data = read(pageFile(feed.dir, feed.ext, page))
    return true
  })
}
