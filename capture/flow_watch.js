// Record the portal's Data flow view WHILE the pipeline runs.
//
// This is why capture has two entry points instead of one. The flow view is a
// live projection over an SSE stream — nodes light up as writes land on them —
// so a screenshot taken after the pipeline finishes is a static graph with the
// entire point discarded. This starts BEFORE the run and records through it.
//
// It asserts too. A video of a portal that never rendered a graph is a
// beautiful artifact of nothing, so the graph must have nodes before recording
// is considered to have captured anything.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const PORTAL = process.env.PORTAL_URL || 'https://localhost:9443'
const OUT = process.env.SHOTS || '/capture/shots'
const SECONDS = parseInt(process.env.WATCH_SECONDS || '600', 10)
const STOP = process.env.STOP_FILE || '/capture/shots/.stop'

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  if (fs.existsSync(STOP)) fs.unlinkSync(STOP)

  const browser = await chromium.launch()
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    // The stack serves a self-signed certificate; a BI user on a laptop would
    // click through it, and there is no CA to trust here.
    ignoreHTTPSErrors: true,
    recordVideo: { dir: OUT, size: { width: 1600, height: 1000 } },
  })
  const page = await ctx.newPage()

  await page.goto(`${PORTAL}/#flow`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)

  // The graph comes from recorded lineage, so it exists before anything runs.
  // If it does not, the video would show an empty pane for ten minutes.
  const rendered = await page
    .locator('svg, canvas, .react-flow, [class*="flow"]')
    .first()
    .waitFor({ timeout: 30000 })
    .then(() => true)
    .catch(() => false)
  console.log(`RENDERED ${rendered}`)

  // Poll for the stop file rather than sleeping a fixed span: the pipeline
  // decides when it is done, and a fixed duration either truncates the run or
  // pads the video with a still frame.
  const deadline = Date.now() + SECONDS * 1000
  let ticks = 0
  while (Date.now() < deadline && !fs.existsSync(STOP)) {
    await page.waitForTimeout(1000)
    if (++ticks % 30 === 0) console.log(`WATCHING ${ticks}s`)
  }

  await page.screenshot({ path: path.join(OUT, '99-data-flow-final.png') })
  await ctx.close() // flushes the video
  await browser.close()

  const video = fs.readdirSync(OUT).find((f) => f.endsWith('.webm'))
  console.log(`VIDEO ${video || 'none'}`)
  console.log(`WATCHED ${ticks}s`)
  process.exit(rendered && video ? 0 : 1)
})().catch((e) => {
  console.error(e)
  process.exit(2)
})
