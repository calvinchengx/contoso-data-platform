// Serve the group data office's two Parquet feeds, whole.
//
// Same shape as the other two vendors and deliberately its own file, for the
// reason contoso-web/serve.js gives: these are separate companies, and a shared
// handler makes one outage everyone's.
//
// WHAT IS DIFFERENT HERE, and it is the whole reason this file needs comments:
// the body is BINARY, and mokapi's ordinary path cannot carry binary at all.
//
//   `read()` returns `string(b)` — a Go string handed to goja, which decodes it
//   as UTF-8. Every byte sequence that is not valid UTF-8 becomes U+FFFD, and
//   `response.body` is a Go `string` written out as `[]byte(response.Body)`.
//   So the round trip is lossy for anything that is not text.
//
//   MEASURED, not assumed: the 2,268-byte fx_rates.parquet comes back 3,301
//   bytes, a 46% inflation, and product_hierarchy.parquet 2,036 -> 2,188. The
//   `PAR1` magic and the `PAR1` footer BOTH survive intact, so the result still
//   looks like Parquet from either end while everything between is shredded.
//
// The one path that carries bytes is `response.data` holding a raw byte slice,
// which mokapi passes through untouched instead of marshalling. `open()` with
// `{as: 'binary'}` is what produces one — note `open` is a GLOBAL, not an
// export of 'mokapi/file', which only offers the lossy `read`.
//
// The vendor also publishes a checksum of what it sent. That is belt and
// braces against exactly the failure above: if this ever silently reverts to
// the text path, ingest_reference fails at the boundary with a message naming
// the transport, rather than bronze failing inside a Parquet reader.
import { on } from 'mokapi'
import { read } from 'mokapi/file'

// The vendor's key, from the fixture that also produced the bytes.
// `make sources` writes it; nothing here restates it.
const KEY = read('/sources/_data/contoso-reference/.api-key').trim()

const FEEDS = {
  exportProductHierarchy: 'product_hierarchy',
  exportFxRates: 'fx_rates',
}

const base = (name) => `/sources/_data/contoso-reference/${name}`

export default function () {
  on('http', function (request, response) {
    const name = FEEDS[request.operationId]
    if (!name) return false

    // Checked BEFORE the file is opened: an unauthenticated caller must not be
    // able to make the vendor do work. Reference data is not automatically
    // public just because it is not transactional.
    if (request.header['X-Api-Key'] !== KEY) {
      response.statusCode = 401
      response.data = 'invalid api key'
      return true
    }

    // A PLAIN LITERAL, never `response.headers[k] = v`: mokapi's headers object
    // is a native binding, and assigning it back to itself throws. mokapi then
    // falls back to SCHEMA GENERATION and answers 200 with invented bytes — a
    // green status carrying garbage. contoso-web/serve.js carries the same note.
    response.headers = {
      'Content-Type': 'application/vnd.apache.parquet',
      // Written by `make sources` beside the file it describes, so the vendor
      // cannot advertise a digest for bytes it is not actually serving.
      'X-Content-SHA256': read(`${base(name)}.sha256`).trim(),
    }
    // `data` with a BYTE SLICE, which is neither of the other vendors' choices
    // and is the only shape that survives — see the note at the top. `body`
    // would corrupt it and `data` with anything but bytes would be marshalled
    // against the response schema, which here is `format: binary`.
    response.data = open(`${base(name)}.parquet`, { as: 'binary' })
    return true
  })
}
