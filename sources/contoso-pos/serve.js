// Serve the vendor's export from the materialised fixture files.
//
// WHY FILES AND NOT mokapi's SCHEMA GENERATION: generation is random per
// request and random in shape — optional properties are dropped per row — so a
// generated body cannot back `assert n_orders == 247_500`. The bytes here come
// from the same seeded generator the fabric-emulator examples assert against,
// which is the entire reason the two repositories agree on their numbers.
//
// Measured through mokapi: 176 MB of CSV in ~1.0s and a Parquet body
// byte-identical to the file on disk, so nothing here is a workaround.
import { on } from 'mokapi'
import { read } from 'mokapi/file'

const KEY = 'contoso-pos-key-7731-dev'

const BODIES = {
  exportCustomers: '/sources/_data/contoso-pos/customers.csv',
  exportOrders: '/sources/_data/contoso-pos/orders.jsonl',
}

export default function () {
  on('http', function (request, response) {
    const path = BODIES[request.operationId]
    if (!path) return false

    // A real 401, replacing the in-process PermissionError the emulator's own
    // example raises. The extract step asserts a wrong key is refused, and it
    // should be refused the way an HTTP client would actually experience it.
    if (request.header['X-Api-Key'] !== KEY) {
      response.statusCode = 401
      response.data = 'invalid api key'
      return true
    }
    response.data = read(path)
    return true
  })
}
