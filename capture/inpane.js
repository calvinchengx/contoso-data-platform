// Record the run inside the portal's OWN terminal pane.
//
// The sibling `sidebyside.js` composites two iframes — a separately launched
// ttyd beside the portal — into one page and films that. This films the portal
// alone, because the emulator now carries the terminal itself: the Flow view
// has a pane that proxies ttyd through the portal's origin, so the terminal and
// the graph are one product rather than two windows a recorder glued together.
//
// WHY THE TERMINAL STILL RUNS ON THE HOST. `platform/gold.py` shells out to
// `docker compose`, so the filmed shell has to drive Docker, hold this repo and
// have `uv`. A shell inside a container could only do that with the host's
// Docker socket mounted, and a socket behind a portal-proxied pane is root on
// the host. The pane is where it is DISPLAYED; the process stays on the host.
//
// ORDER IS STILL FREE. ttyd spawns its command when a client connects, and the
// client here is the pane — so the run begins when the recorder clicks Connect.
// Provisioning and the vendor pull, the steps that explain what follows, are on
// camera rather than missed by a late-attaching recorder.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const OUT = '/capture/shots'
const STOP = path.join(OUT, '.stop')
const PORTAL = process.env.PORTAL_URL || 'https://localhost:9443'
const OM = process.env.OM_URL || 'http://localhost:18587'  // matches compose/governance.yml's OM_PORT default
const TOKEN = process.env.TERMINAL_TOKEN || ''
const SECONDS = Number(process.env.MAX_SECONDS || 1800)
const W = Number(process.env.WIDTH || 1600)
const H = Number(process.env.HEIGHT || 900)

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  if (fs.existsSync(STOP)) fs.unlinkSync(STOP)
  if (!TOKEN) {
    console.error('TERMINAL_TOKEN is required — the pane will not connect without it')
    process.exit(2)
  }

  const browser = await chromium.launch()
  const ctx = await browser.newContext({
    viewport: { width: W, height: H },
    // Self-signed, as the whole stack is; a user on a laptop clicks through it.
    ignoreHTTPSErrors: true,
    // Dark, and dark from the FIRST FRAME. `colorScheme` is what the portal's
    // 'system' preference resolves against, and it also darkens scrollbars and
    // form controls — a light scrollbar down the side of a dark recording is
    // the kind of detail that reads as a mistake.
    colorScheme: 'dark',
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  })
  // Set the preference BEFORE the page's own scripts run. index.html stamps
  // `data-theme` on <html> before the bundle loads precisely so the first paint
  // is correct; clicking the toggle after load would film a white flash first.
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('fe.theme', 'dark')
    } catch {}
  })
  const page = await ctx.newPage()
  // Every non-2xx THIS page sees, in the recorder's own log. The 404 banner
  // has appeared only on the recorder's page — never on a plain observer of
  // the same portal — so the recorder is the only client that can name it.
  page.on('response', async (r) => {
    if (r.status() < 400) return
    let body = ''
    try {
      body = (await r.text()).slice(0, 120)
    } catch {}
    console.log(`NON2XX ${r.status()} ${r.request().method()} ${r.url()}  body=${JSON.stringify(body)}`)
  })
  // The banner probe, and its PROOF THAT IT CAN SEE.
  //
  // The 404 banner was hunted for five runs with a pixel detector calibrated on
  // the light palette's pink (#fde7e9). In dark mode that banner is #3b1417 —
  // near-black — so a colour detector carried over unchanged would report a
  // clean recording no matter what appeared on screen. This reads the DOM
  // instead, which is the same in both themes.
  const bannerProbe = () =>
    Array.from(document.querySelectorAll('.error, .banner-error'))
      .filter((el) => el.offsetParent !== null)
      .map((el) => el.textContent.trim().slice(0, 120))
      .filter(Boolean)

  // Calibrated off-camera in a context that records nothing: a detector that
  // has never been shown a positive is indistinguishable from one that is
  // broken, and this whole file exists because of a check that could not fail.
  {
    const scratch = await browser.newContext()
    const probePage = await scratch.newPage()
    await probePage.setContent('<p class="error">CALIBRATION</p>')
    const seen = await probePage.evaluate(bannerProbe)
    await scratch.close()
    if (seen.length !== 1 || !seen[0].includes('CALIBRATION')) {
      console.error(`banner probe cannot see a banner it was handed: ${JSON.stringify(seen)}`)
      process.exit(2)
    }
    console.log('BANNERPROBE calibrated — a visible .error is detected')
  }

  await page.goto(`${PORTAL}/#flow`, { waitUntil: 'domcontentloaded' })

  // Fold the sidebar first. The whole point of the in-pane recording is the
  // terminal, the graph and the events sharing one screen, and at 1600px the
  // 240px of navigation is what the right-hand column gives up for it.
  await page
    .getByRole('button', { name: 'Toggle sidebar' })
    .click()
    .catch(() => console.error('no sidebar toggle — portal predates the split layout'))

  // The toggle only appears when the emulator can actually REACH ttyd — it
  // dials rather than trusting its own configuration. So waiting for the button
  // is waiting for a working terminal, and its absence is a real diagnosis
  // rather than a slow render.
  const toggle = page.getByRole('button', { name: 'Terminal' })
  try {
    await toggle.waitFor({ state: 'visible', timeout: 30000 })
  } catch {
    const why = await page
      .locator('text=/Terminal unavailable/')
      .textContent()
      .catch(() => null)
    console.error(
      why
        ? `the portal says: ${why}`
        : 'no Terminal toggle — is FABRIC_TERMINAL_URL set (compose/terminal.yml) ' +
            'and is ttyd running on the host?',
    )
    process.exit(2)
  }
  await toggle.click()

  await page.getByLabel('terminal token').fill(TOKEN)
  await page.getByRole('button', { name: 'Connect' }).click()
  console.log('CONNECTED pane opened — ttyd spawns the command now')

  const frameFor = () =>
    page.frames().find((f) => f.url().includes('/_emulator/portal/terminal/'))

  let maxNodes = 0
  let termLines = 0
  let lastLine = ''
  const banners = []

  // The terminal is read from xterm's own BUFFER. xterm.js renders to <canvas>,
  // so DOM queries match nothing and a pixel sample reads back one flat colour —
  // the buffer is the only surface that says what the terminal SHOWS. The
  // sibling recorder learned this by reporting a false negative on a recording
  // where the pane was working perfectly.
  const sample = async () => {
    const f = frameFor()
    if (f) {
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
    }
    // Same page, not a frame: the graph is the portal's own DOM here.
    const n = await page.locator('svg g.node').count().catch(() => 0)
    if (n > maxNodes) maxNodes = n
    // Any banner that appears between two samples is named here and now, while
    // its text still says what failed — a frame scanned afterwards can only say
    // that something red was there.
    for (const b of await page.evaluate(bannerProbe).catch(() => [])) {
      if (!banners.includes(b)) {
        banners.push(b)
        console.log(`BANNER ${b}`)
      }
    }
  }

  await sample()

  const deadline = Date.now() + SECONDS * 1000
  let ticks = 0
  while (Date.now() < deadline && !fs.existsSync(STOP)) {
    await page.waitForTimeout(1000)
    ticks++
    if (ticks % 5 === 0) await sample()
    if (ticks % 60 === 0)
      console.log(
        `WATCHING ${ticks}s  nodes=${maxNodes} termLines=${termLines} | ${lastLine.slice(0, 90)}`,
      )
  }
  await sample()

  // Both halves are asserted separately: a recording where the graph filled in
  // but the terminal never attached is half the story, and it looks fine in a
  // thumbnail.
  const rendered = maxNodes > 0
  const attached = termLines > 0
  // What the portal actually painted, not what was asked for: `data-theme` is
  // the attribute every dark rule keys off, so reading it back is the one
  // statement that the recording IS dark.
  const themeShown = await page
    .evaluate(() => document.documentElement.dataset.theme)
    .catch(() => 'unknown')
  console.log(`RENDERED ${rendered} (max nodes on screen: ${maxNodes})`)
  console.log(`TERMINAL ${attached} (max non-empty buffer lines: ${termLines})`)
  console.log(`THEME ${themeShown}`)
  console.log(`BANNERS ${banners.length}`)
  console.log(`LASTLINE ${lastLine.slice(0, 120)}`)

  await page.screenshot({ path: path.join(OUT, '99-in-pane-final.png') })

  // --- the tour: what the run BUILT, in the same take ---------------------
  // The flow view shows the platform running; it says nothing about what came
  // out. So after the run, the camera keeps rolling and walks the two result
  // surfaces — the semantic model (the Power BI-shaped artifact) and the
  // OpenMetadata catalog with its lineage back to the vendors.
  //
  // BEST-EFFORT BY DESIGN, and the honesty is in the log line: the pages
  // toured here are ASSERTED by om_verify.js and semantic_model.py, which fail
  // the run when they lie. A missed selector here costs a scene, not the
  // recording — but TOURED false is printed so a silent shrink is visible.
  // Budget: demo.py allows the recorder 300s after the stop marker.
  let toured = 0
  let attempted = 0
  const dwell = (ms) => page.waitForTimeout(ms)
  // One scene, one try: a slow page costs its own scene, never the rest of the
  // tour. Every URL here is a pattern om_verify.js asserts — nothing in this
  // tour films a page that is not proven to exist.
  const scene = async (what, ms, go) => {
    attempted++
    try {
      await go()
      await dwell(ms)
      toured++
    } catch (e) {
      console.error(`scene "${what}" skipped: ${String(e).slice(0, 160)}`)
    }
  }
  const om = (p) => page.goto(OM + p, { waitUntil: 'domcontentloaded' })

  // The semantic model: the list, then the model itself — its Direct Lake
  // bindings and every measure's DAX.
  //
  // NO TYPED QUERY HERE ANY MORE, and the reason is a real improvement rather
  // than a lost scene. Gold is now read IN PLACE by Direct Lake instead of the
  // model carrying a copy of it in `data.json`, and the portal's query runner
  // refuses Direct Lake by design: reading lake data needs a principal and an
  // unauthenticated portal has none. Typing a query here would film the
  // refusal.
  //
  // The live DAX answer comes from the terminal instead — `make reconcile`
  // asks the model through `executeQueries`, the route that error message
  // names, and grades the result against the same figures computed straight
  // from the warehouse in SQL. That is a stronger frame than the old one: the
  // number is not just returned, it is cross-checked on camera against a
  // surface that shares no code with it.
  await scene('models', 4000, () => page.goto(`${PORTAL}/#models`, { waitUntil: 'domcontentloaded' }))
  await scene('ContosoRevenue', 6000, async () => {
    const model = page.locator('text=ContosoRevenue').first()
    if (await model.count()) await model.click()
  })
  await scene('Direct Lake bindings', 7000, async () => {
    // Scroll the measures into frame: the page opens on the column list, and
    // what earns the screen time is the DAX beside each measure and the
    // `Direct Lake` badge naming where the rows actually live.
    const badge = page.locator('text=Direct Lake').first()
    if (await badge.count()) await badge.scrollIntoViewIfNeeded()
    await page.mouse.wheel(0, 600)
  })

  // The theme control, shown rather than claimed — and shown from where this
  // recording sits. The cycle is system -> light -> dark, so from DARK three
  // clicks are: dark -> system (still dark, the OS is dark here), system ->
  // light (the palette this recording is not in), light -> dark. The portal
  // ends the tour the way the recording started it.
  await scene('theme toggle', 5000, async () => {
    const toggle = page.locator('button[aria-label^="Theme:"]').first()
    await toggle.waitFor({ state: 'visible', timeout: 5000 })
    await toggle.click()
    await dwell(1500)
    await toggle.click()
    await dwell(3000) // the light palette, held long enough to read
    await toggle.click()
    const back = await page.evaluate(() => document.documentElement.dataset.theme)
    if (back !== 'dark') throw new Error(`theme left as ${back}, not dark`)
  })

  // The catalog. Same login dance as om_verify.js, same admin bootstrap.
  await scene('catalog login', 2500, async () => {
    await page.goto(OM, { waitUntil: 'domcontentloaded' })
    await page.fill('#email', 'admin@open-metadata.org', { timeout: 15000 })
    await page.fill('#password', 'admin')
    await page.click('button[type="submit"]')
    await page.waitForLoadState('networkidle').catch(() => {})
  })

  // The vendors as catalog entities: the REST source and its endpoints, the
  // relational source and its table, the stream between them.
  await scene('contoso-pos service', 5000, () => om('/service/apiServices/contoso-pos'))
  await scene('pos endpoints', 6000, () => om('/apiCollection/' + encodeURIComponent('contoso-pos.export')))
  await scene('contoso-erp service', 4000, () => om('/service/databaseServices/contoso-erp'))
  await scene('erp customer table', 5000, () => om('/table/' + encodeURIComponent('contoso-erp.erp.erp.customer')))
  await scene('source-to-topic lineage', 9000, () =>
    om('/table/' + encodeURIComponent('contoso-erp.erp.erp.customer') + '/lineage'))
  await scene('redpanda service', 4000, () => om('/service/messagingServices/contoso-redpanda'))

  // The star itself, and the lineage that walks it back to the vendors — the
  // frame the whole platform exists to earn.
  const star = 'contoso-fabric.contoso-analytics.warehouse.fct_revenue_summary'
  await scene('fct_revenue_summary', 6000, () => om('/table/' + encodeURIComponent(star)))
  await scene('star lineage', 10000, () => om('/table/' + encodeURIComponent(star) + '/lineage'))

  console.log(`TOURED ${toured}/${attempted} scenes`)

  // Claim this run's video by name before the context closes: Playwright names
  // them randomly, so a directory scan afterwards can return the PREVIOUS run's
  // file — which once made this check pass on a recording that never happened.
  const dest = path.join(OUT, '99-in-pane.webm')
  const video = page.video()
  await ctx.close() // flushes the video; must precede saveAs
  let saved = false
  if (video) {
    await video.saveAs(dest)
    await video.delete()
    saved = fs.existsSync(dest)
  }
  await browser.close()

  console.log(`VIDEO ${saved ? '99-in-pane.webm' : 'none'}`)
  console.log(`WATCHED ${ticks}s`)
  process.exit(rendered && attached && saved ? 0 : 1)
})().catch((e) => {
  console.error(e)
  process.exit(2)
})
