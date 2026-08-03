// Verify the catalog, THEN capture it.
//
// A screenshot suite that only captures will happily produce a beautiful
// picture of an empty catalog: the run stays green and the artifact is
// worthless. So every shot below is preceded by an assertion on the page's
// TEXT — if the entity is not really there, this fails before it photographs
// anything.
//
// What it checks is the claim this platform exists to make: the SOURCE SYSTEMS
// are catalogued, not just the tables downstream of them.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const OM = process.env.OM_URL || 'http://localhost:8585'
const OUT = process.env.SHOTS || '/capture/shots'

const checks = []
function check(name, ok, detail) {
  checks.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`)
}

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await ctx.newPage()

  await page.goto(OM, { waitUntil: 'domcontentloaded' })
  await page.fill('#email', 'admin@open-metadata.org')
  await page.fill('#password', 'admin')
  await page.click('button[type="submit"]')
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(4000)
  check('signed in', !/signin/i.test(page.url()), page.url())

  // Numbered so the set reads as a narrative rather than a pile of PNGs.
  let n = 0
  const shot = async (name) =>
    page.screenshot({ path: path.join(OUT, `${String(++n).padStart(2, '0')}-${name}.png`) })

  const visit = async (url, waitFor) => {
    await page.goto(OM + url, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(3000)
    if (waitFor) await page.waitForSelector(waitFor, { timeout: 15000 }).catch(() => {})
    return (await page.locator('body').innerText()).replace(/\s+/g, ' ')
  }

  // 1. The REST source, with its endpoints — the entity type a catalog that
  //    starts at bronze never has.
  let t = await visit('/service/apiServices/contoso-pos')
  check('POS is an API service', /contoso-pos/i.test(t))
  await shot('contoso-pos-api-service')

  // The endpoints hang off the COLLECTION, not the service page — asserting on
  // the service and finding nothing says the endpoints are missing when they
  // are simply one level down.
  // The UI lists endpoints by DISPLAY NAME — the OpenAPI `summary` — not by
  // operationId. Asserting on the operationId failed while the catalog was
  // perfectly correct, which is why this checks what a user actually sees:
  // the count, the summaries, and the HTTP method.
  t = await visit('/apiCollection/' + encodeURIComponent('contoso-pos.export'))
  check('the collection reports both endpoints', /Endpoints\s*2/i.test(t))
  check('its endpoints are listed',
        /Full customer snapshot/i.test(t) && /Orders since the last export/i.test(t),
        'by their OpenAPI summaries')
  check('their HTTP method is shown', /GET/.test(t))
  await shot('contoso-pos-endpoints')

  // 2. The relational source.
  t = await visit('/service/databaseServices/contoso-erp')
  check('ERP is a database service', /contoso-erp/i.test(t))
  await shot('contoso-erp-database-service')

  t = await visit('/table/' + encodeURIComponent('contoso-erp.erp.erp.customer'))
  check('the ERP table carries its columns',
        /erp_customer_id/i.test(t) && /credit_band/i.test(t))
  await shot('contoso-erp-customer-table')

  // 3. The change stream.
  t = await visit('/service/messagingServices/contoso-redpanda')
  check('the change stream is a messaging service', /contoso-redpanda/i.test(t))
  await shot('contoso-redpanda-messaging-service')

  // 4. The edge that makes the vendor upstream of everything.
  t = await visit('/table/' + encodeURIComponent('contoso-erp.erp.erp.customer') + '/lineage',
                  '[data-testid="lineage-details"], .react-flow')
  const nodes = await page.locator('.react-flow__node').count().catch(() => 0)
  check('lineage graph has more than one node', nodes > 1, `${nodes} node(s)`)
  await shot('lineage-source-to-topic')

  await browser.close()

  const failed = checks.filter((c) => !c.ok)
  console.log(`\n${checks.length - failed.length}/${checks.length} checks passed, ` +
              `${fs.readdirSync(OUT).length} screenshots in ${OUT}`)
  process.exit(failed.length ? 1 : 0)
})().catch((e) => {
  console.error(e)
  process.exit(2)
})
