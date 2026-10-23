/* Optional GPU regression check. Requires Playwright and an offline HTML export.
 * NODE_PATH=/path/to/node_modules node scripts/check_translucency.cjs map.3d.html
 * Set CHROME_PATH when Chrome is not installed at the default Linux location.
 */
const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path");
const { chromium } = require("playwright");
(async () => {
  if (!process.argv[2]) throw new Error("Pass an offline 3D HTML export");
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "cartogram-translucency-"),
  );
  let browser;
  try {
    const html = fs
      .readFileSync(process.argv[2], "utf8")
      .replace(
        /window\.cartogramViewer\s*=\s*\{/,
        "window.__renderTest={meshes,render,renderer};window.cartogramViewer={",
      );
    const target = path.join(directory, "test.html");
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
        viewport: { width: 1280, height: 900 },
      }),
      errors = [],
      requests = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    page.on("request", (r) => {
      if (/^https?:/.test(r.url())) requests.push(r.url());
    });
    await page.goto("file://" + target);
    await page.waitForFunction(() => window.__renderTest);
    const stats = await page.evaluate(() => {
      const { meshes, render, renderer } = window.__renderTest;
      const read = () => {
        render();
        const gl = renderer.getContext(),
          pixels = new Uint8Array(
            gl.drawingBufferWidth * gl.drawingBufferHeight * 4,
          );
        gl.readPixels(
          0,
          0,
          gl.drawingBufferWidth,
          gl.drawingBufferHeight,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          pixels,
        );
        return pixels;
      };
      const slider = document.getElementById("opacity");
      const opacity = (value) => {
        slider.value = value;
        slider.dispatchEvent(new Event("input"));
      };
      opacity(40);
      const normal = read();
      meshes.forEach((m, i) => (m.renderOrder = 1000 - i));
      const reversed = read();
      opacity(100);
      const opaque = read();
      let error = 0,
        changed = 0;
      for (let i = 0; i < normal.length; i += 4) {
        let difference = 0;
        for (let k = 0; k < 3; k++) {
          error += Math.abs(normal[i + k] - reversed[i + k]);
          difference = Math.max(
            difference,
            Math.abs(normal[i + k] - opaque[i + k]),
          );
        }
        if (difference > 20) changed++;
      }
      opacity(20);
      return {
        meanOrderError: error / ((normal.length / 4) * 3 * 255),
        changedPixels: changed,
        opacity: window.cartogramViewer.opacity,
        transparency: window.cartogramViewer.transparency,
      };
    });
    if (stats.changedPixels < 1000)
      throw new Error("Opacity did not visibly change enough pixels");
    if (
      stats.transparency === "weighted blended order independent" &&
      stats.meanOrderError > 0.002
    )
      throw new Error("Transparency depends on country draw order");
    if (stats.opacity !== 0.2)
      throw new Error("Opacity control did not update material");
    await page.getByRole("button", { name: "Top view", exact: true }).click();
    await page.getByRole("button", { name: "3D view", exact: true }).click();
    if ((await page.evaluate(() => window.cartogramViewer.opacity)) !== 0.2)
      throw new Error("Changing view lost opacity");
    if (errors.length || requests.length)
      throw new Error(JSON.stringify({ errors, requests }));
    console.log(JSON.stringify(stats, null, 2));
  } finally {
    if (browser) await browser.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
