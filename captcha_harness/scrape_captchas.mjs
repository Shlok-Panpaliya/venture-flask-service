// Drives the live Bhulekh page with Puppeteer, harvesting captcha images by
// repeatedly clicking the refresh button. Saves each PNG + a manifest.json.
//
// Usage: node scrape_captchas.mjs [count] [outDir]
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const URL = "https://bhulekh.mahabhumi.gov.in/NewBhulekh.aspx";
const IMG_ID = "#ContentPlaceHolder1_captchaImage";
const REFRESH_ID = "#ContentPlaceHolder1_btnreferesh";

const COUNT = parseInt(process.argv[2] || "20", 10);
const OUT = process.argv[3] || path.join(__dirname, "captchas");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getSrc = (page) => page.$eval(IMG_ID, (el) => el.getAttribute("src") || "");

async function waitForDataSrc(page, timeout = 20000) {
  await page.waitForFunction(
    (id) => {
      const el = document.querySelector(id);
      const s = el && el.getAttribute("src");
      return s && s.startsWith("data:image") && s.length > 120;
    },
    { timeout },
    IMG_ID
  );
  return getSrc(page);
}

async function waitForNewSrc(page, oldSrc, timeout = 20000) {
  await page.waitForFunction(
    (id, old) => {
      const el = document.querySelector(id);
      const s = el && el.getAttribute("src");
      return s && s.startsWith("data:image") && s.length > 120 && s !== old;
    },
    { timeout },
    IMG_ID,
    oldSrc
  );
  return getSrc(page);
}

function saveDataUrl(dataUrl, file) {
  const b64 = dataUrl.split(",", 2)[1];
  fs.writeFileSync(file, Buffer.from(b64, "base64"));
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({
  headless: "new",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });

const manifest = [];
try {
  await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });
  let src = await waitForDataSrc(page);

  for (let i = 1; i <= COUNT; i++) {
    const file = path.join(OUT, `cap_${String(i).padStart(2, "0")}.png`);
    saveDataUrl(src, file);
    manifest.push({ index: i, file: path.basename(file) });
    process.stdout.write(`saved ${path.basename(file)}\n`);

    if (i < COUNT) {
      const prev = src;
      try {
        await Promise.all([
          page.click(REFRESH_ID),
          page.waitForNavigation({ waitUntil: "networkidle2", timeout: 15000 }).catch(() => {}),
        ]);
        src = await waitForNewSrc(page, prev);
      } catch (e) {
        process.stdout.write(`  refresh retry (${e.message.split("\n")[0]})\n`);
        await sleep(800);
        await page.click(REFRESH_ID).catch(() => {});
        src = await waitForDataSrc(page);
      }
      await sleep(400);
    }
  }
} catch (err) {
  console.error("SCRAPE ERROR:", err.message);
} finally {
  fs.writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2));
  await browser.close();
}
console.log(`\nDone. ${manifest.length} captchas saved in ${OUT}`);
