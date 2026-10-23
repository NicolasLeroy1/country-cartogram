/* Check continuous playback, encoded areas, imputation colours and year controls.
 * NODE_PATH=/path/to/node_modules node scripts/check_timeline.cjs timeline.html
 */
const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  if (!process.argv[2]) throw new Error("Pass an offline timeline HTML export");
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "cartogram-timeline-"),
  );
  let browser;
  try {
    const html = fs
      .readFileSync(process.argv[2], "utf8")
      .replace(
        /window\.cartogramViewer\s*=\s*\{/,
        "window.__timelineTest={data,meshes,transforms,values,estimateWeights,valueStatuses,camera,controls};window.cartogramViewer={",
      );
    const target = path.join(directory, "timeline.html");
    fs.writeFileSync(target, html);
    browser = await chromium.launch({
      executablePath: process.env.CHROME_PATH || "/opt/google/chrome/chrome",
      args: [
        "--no-sandbox",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
      ],
    });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    const errors = [],
      requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    page.on("request", (r) => {
      if (/^https?:/.test(r.url())) requests.push(r.url());
    });
    await page.goto("file://" + target);
    await page.waitForFunction(() => window.__timelineTest);
    const info = await page.evaluate(() => {
      const { data } = window.__timelineTest;
      return {
        regions: data.regions.length,
        observations: data.endpoints.length,
        first: Number(data.frames[data.endpoints[0]].year),
        last: Number(data.frames[data.endpoints.at(-1)].year),
        estimated: data.hasEstimates,
        mode: data.mode,
      };
    });
    assert.ok(info.observations > 1);
    assert.equal(await page.locator("#year option").count(), info.observations);
    await page.locator("summary").click();
    const firstValues = await page.locator("#values tr").first().innerText();
    async function scrub(time) {
      await page.locator("#scrub").evaluate((input, value) => {
        input.value = value;
        input.dispatchEvent(new Event("input"));
      }, time);
    }
    async function encodingError() {
      return page.evaluate(() => {
        const { data, meshes, values } = window.__timelineTest;
        const first = data.observations[0],
          baseline = first.reduce((s, r) => s + r[0], 0),
          current = values.reduce((s, v, i) => s + (i % 3 === 0 ? v : 0), 0);
        const base = new Float32Array(
          Uint8Array.from(atob(data.transforms), (c) => c.charCodeAt(0)).buffer,
        );
        let area = 0,
          height = 0;
        for (let i = 0; i < meshes.length; i++) {
          const expected =
            data.normalization === "shares"
              ? values[i * 3] / current / (first[i][0] / baseline)
              : values[i * 3] / first[i][0];
          area = Math.max(
            area,
            Math.abs((meshes[i].scale.x / base[i * 4 + 2]) ** 2 / expected - 1),
          );
          if (data.mode === "3d" && values[i * 3 + 1] > 0)
            height = Math.max(
              height,
              Math.abs(
                meshes[i].scale.z / (data.heightScale * values[i * 3 + 1]) - 1,
              ) || 0,
            );
        }
        return { area, height };
      });
    }
    await scrub(info.first + (info.last - info.first) * 0.37137);
    const middle = await encodingError();
    assert.ok(
      middle.area < 2e-5 && middle.height < 2e-5,
      JSON.stringify(middle),
    );
    assert.equal(
      await page.evaluate(() => window.cartogramViewer.observation),
      false,
    );
    assert.match(
      await page.locator("#table-period").innerText(),
      /interpolated/,
    );
    await page.locator("#year").selectOption(String(info.last));
    assert.equal(
      await page.evaluate(() => window.cartogramViewer.observation),
      true,
    );
    assert.notEqual(
      await page.locator("#values tr").first().innerText(),
      firstValues,
    );
    const end = await encodingError();
    assert.ok(end.area < 2e-5 && end.height < 2e-5, JSON.stringify(end));
    if (info.estimated) {
      const colours = await page.evaluate(() => {
        const { data, meshes, estimateWeights } = window.__timelineTest;
        let estimated = 0,
          observed = 0;
        for (let i = 0; i < meshes.length; i++) {
          const colour = meshes[i].material.color.getHexString();
          if (estimateWeights[i] === 1) {
            if (colour !== "a0a5a9") throw new Error("Estimate not grey");
            estimated++;
          } else if (estimateWeights[i] === 0) {
            if (colour !== data.regions[i].color.slice(1))
              throw new Error("Observed country lost group colour");
            observed++;
          }
        }
        return { estimated, observed };
      });
      assert.ok(colours.estimated > 0 && colours.observed > 0);
      assert.equal(await page.locator("#estimate-key").isVisible(), true);
    }
    await page.locator("summary").click();
    await scrub(info.first);
    await page.locator("#play").click();
    await page.waitForFunction(
      (start) => window.cartogramViewer.time > start + 0.2,
      info.first,
    );
    await page.locator("#play").click();
    const progress = await page.evaluate(() => ({
      time: window.cartogramViewer.time,
      frame: window.cartogramViewer.framePosition,
    }));
    assert.ok(progress.frame % 1 > 0 && progress.time > info.first);
    await page.locator("#year").selectOption(String(info.last));
    if (process.argv[3])
      await page.locator("#viewport").screenshot({ path: process.argv[3] });
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    console.log(
      JSON.stringify(
        {
          ...info,
          areaError: Math.max(middle.area, end.area),
          heightError: Math.max(middle.height, end.height),
          errors,
          requests,
        },
        null,
        2,
      ),
    );
  } finally {
    if (browser) await browser.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
