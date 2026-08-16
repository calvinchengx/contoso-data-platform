// Record the terminal and the flow graph in ONE frame, advancing together.
//
// WHY BOTH PANES. `flow_watch.js` films the graph alone, and a graph filling in
// is unreadable without the thing causing it: a viewer sees boxes appear and
// has no idea whether that took two seconds or two minutes, or which step did
// it. Side by side, the terminal line and the node that lights up are the same
// event seen twice, and the video explains itself without narration.
//
// setContent rather than a static server: the page is two iframes and a header,
// and standing up an HTTP server to serve six lines of HTML would be one more
// thing that can fail between the pipeline and the recording. Cross-origin
// iframes render fine from about:blank; nothing here reads across them.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const PORTAL = process.env.PORTAL_URL || 'https://localhost:9443'
const TTYD = process.env.TTYD_URL || 'http://localhost:7681'
const OUT = process.env.SHOTS || '/capture/shots'
const SECONDS = parseInt(process.env.WATCH_SECONDS || '2400', 10)
const STOP = process.env.STOP_FILE || '/capture/shots/.stop'

const W = 1920
const H = 1080

const PAGE = `
<style>
  html,body { margin:0; height:100%; background:#0b0e14;
              font-family:-apple-system,'Segoe UI',sans-serif; }
  .wrap { display:flex; flex-direction:column; height:100vh; }
  header { display:flex; height:38px; line-height:38px; font-size:13px;
           border-bottom:1px solid #1f2430; color:#9aa4b2; flex:none; }
  header div { flex:1; padding:0 16px; white-space:nowrap; overflow:hidden; }
  header b { color:#e6e6e6; font-weight:600; }
  main { flex:1; display:flex; min-height:0; }
  iframe { flex:1; border:0; min-width:0; height:100%; background:#fff; }
  iframe.term { background:#0b0e14; }
  .divider { width:2px; background:#1f2430; flex:none; }
</style>
<div class="wrap">
  <header>
    <div><b>terminal</b> — contoso-fabric-platform&nbsp; $ make verify</div>
    <div id="right"><b>fabric-emulator portal</b> — Data flow&nbsp; (${PORTAL}/#flow)</div>
  </header>
  <main>
    <iframe class="term" src="${TTYD}"></iframe>
    <div class="divider"></div>
    <iframe id="portal" src="${PORTAL}/#flow"></iframe>
  </main>
</div>`

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  if (fs.existsSync(STOP)) fs.unlinkSync(STOP)

  const browser = await chromium.launch()
  const ctx = await browser.newContext({
    viewport: { width: W, height: H },
    // The stack serves a self-signed certificate; a BI user on a laptop would
    // click through it, and there is no CA to trust here.
    ignoreHTTPSErrors: true,
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  })
  const page = await ctx.newPage()
  await page.setContent(PAGE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)

  // BOTH panes must have shown something, and they are asserted separately.
  //
  // A recording where the graph rendered but the terminal never attached is a
  // video of half the story, and it looks fine in a thumbnail.
  //
  // The terminal is read from xterm's own BUFFER, not the DOM and not pixels.
  // xterm.js renders to <canvas>, so `.xterm-rows > div` matches nothing and
  // innerText is empty — an assertion written against those cannot pass, which
  // is how the first version of this reported TERMINAL false on a recording
  // where the pane was working perfectly. Sampling the canvas is no better:
  // the readback comes back a single flat colour. The buffer is the only
  // surface that says what the terminal actually SHOWS, which is the claim.
  //
  // Sampled through the run rather than checked once: on a clean workspace the
  // graph is empty at t=0 and fills a minute later, so an up-front assertion
  // fails on exactly the run this exists to record.
  let maxNodes = 0
  let termLines = 0
  let lastLine = ''

  const sample = async () => {
    for (const f of page.frames()) {
      if (f.url().startsWith(TTYD)) {
        const r = await f
          .evaluate(() => {
            const buf = window.term?.buffer?.active
            if (!buf) return { n: 0, last: '' }
            let n = 0
            let last = ''
            for (let i = 0; i < buf.length; i++) {
              const s = buf.getLine(i)?.translateToString(true)?.trim() || ''
              if (s) {
                n++
                last = s
              }
            }
            return { n, last }
          })
          .catch(() => ({ n: 0, last: '' }))
        if (r.n > termLines) termLines = r.n
        if (r.last) lastLine = r.last
      } else if (f.url().startsWith(PORTAL)) {
        const n = await f.locator('svg g.node').count().catch(() => 0)
        if (n > maxNodes) maxNodes = n
      }
    }
  }

  await sample()

  // Poll for the stop file rather than sleeping a fixed span: the pipeline
  // decides when it is done, and a fixed duration either truncates the run or
  // pads the video with a still frame.
  const deadline = Date.now() + SECONDS * 1000
  let ticks = 0
  while (Date.now() < deadline && !fs.existsSync(STOP)) {
    await page.waitForTimeout(1000)
    ticks++
    // Sampled, not continuous: a locator query every second would contend with
    // the very rendering being recorded.
    if (ticks % 5 === 0) await sample()
    if (ticks % 60 === 0)
      console.log(`WATCHING ${ticks}s  nodes=${maxNodes} termLines=${termLines} | ${lastLine.slice(0, 90)}`)
  }

  // One last look, for a run that finishes between samples.
  await sample()

  // THE FINALE. The flow graph shows the data MOVING; it does not show what the
  // move was for. A viewer who watches nine minutes of bronze and silver fill in
  // and never sees the star or the semantic model has been shown the plumbing
  // and not the point.
  //
  // So once the pipeline is done — and only then, because these views are empty
  // until it is — the right pane walks the surfaces the run produced, ending on
  // the semantic model that a BI client queries. The terminal keeps its final
  // output on screen throughout (demo.py holds the shell open), so the two
  // panes stay a matched pair rather than one going stale.
  const TOUR = [
    ['#flow', 'Data flow — every hop the emulator observed'],
    ['#warehouse', 'Warehouse SQL — the TDS endpoint dbt built gold through'],
    ['#models', 'Semantic models — what Power BI and XMLA query'],
  ]
  for (const [hash, label] of TOUR) {
    await page.evaluate(
      ([h, l, portal]) => {
        document.getElementById('portal').src = portal + '/' + h
        document.getElementById('right').innerHTML =
          '<b>fabric-emulator portal</b> — ' + l
      },
      [hash, label, PORTAL],
    )
    // Long enough to read, and long enough for the view to fetch and render.
    await page.waitForTimeout(7000)
    await page.screenshot({ path: path.join(OUT, `98-portal-${hash.slice(1)}.png`) })
    console.log(`TOURED ${hash}`)
  }

  const rendered = maxNodes > 0
  const attached = termLines > 0
  console.log(`RENDERED ${rendered} (max nodes on screen: ${maxNodes})`)
  console.log(`TERMINAL ${attached} (max non-empty buffer lines: ${termLines})`)
  console.log(`LASTLINE ${lastLine.slice(0, 120)}`)

  await page.screenshot({ path: path.join(OUT, '99-side-by-side-final.png') })

  // Claim THIS run's video by name before the context closes. Playwright names
  // videos randomly, so a scan of the directory afterwards returns whichever
  // file sorts first — which on the second run is the PREVIOUS run's video.
  // That made this check pass once on a recording that had not happened.
  const dest = path.join(OUT, '99-side-by-side.webm')
  const video = page.video()
  await ctx.close() // flushes the video; must precede saveAs
  let saved = false
  if (video) {
    await video.saveAs(dest)
    await video.delete()
    saved = fs.existsSync(dest)
  }
  await browser.close()

  console.log(`VIDEO ${saved ? '99-side-by-side.webm' : 'none'}`)
  console.log(`WATCHED ${ticks}s`)
  process.exit(rendered && attached && saved ? 0 : 1)
})().catch((e) => {
  console.error(e)
  process.exit(2)
})
