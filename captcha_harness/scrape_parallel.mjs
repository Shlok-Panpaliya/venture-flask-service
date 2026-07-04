// Parallel Bhulekh captcha harvester. Runs N isolated browser contexts (independent
// ASP.NET sessions) concurrently, each looping refresh -> save. Much faster than serial.
//
// Usage: node scrape_parallel.mjs [total] [outDir] [workers]
import fs from "fs";
import path from "path";
import crypto from "crypto";
import { fileURLToPath } from "url";
import puppeteer from "puppeteer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const URL = "https://bhulekh.mahabhumi.gov.in/NewBhulekh.aspx";
const IMG_ID = "#ContentPlaceHolder1_captchaImage";
const REFRESH_ID = "#ContentPlaceHolder1_btnreferesh";

const TOTAL = parseInt(process.argv[2] || "1600", 10);
const OUT = process.argv[3] || path.join(__dirname, "dataset");
const WORKERS = parseInt(process.argv[4] || "6", 10);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getSrc = (page) => page.$eval(IMG_ID, (el) => el.getAttribute("src") || "");
const md5 = (buf) => crypto.createHash("md5").update(buf).digest("hex");

async function waitDataSrc(page, timeout = 20000) {
  await page.waitForFunction(
    (id) => { const s = document.querySelector(id)?.getAttribute("src") || ""; return s.startsWith("data:image") && s.length > 120; },
    { timeout }, IMG_ID);
  return getSrc(page);
}
async function waitNewSrc(page, old, timeout = 12000) {
  await page.waitForFunction(
    (id, o) => { const s = document.querySelector(id)?.getAttribute("src") || ""; return s.startsWith("data:image") && s.length > 120 && s !== o; },
    { timeout }, IMG_ID, old);
  return getSrc(page);
}

const seen = new Set();       // global content-hash dedupe
let saved = 0;
const manifest = [];

async function worker(browser, id, quota) {
  const ctx = await browser.createBrowserContext();      // isolated cookies -> own session
  const page = await ctx.newPage();
  await page.setViewport({ width: 1000, height: 800 });
  let got = 0, sinceNav = 0;
  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
    let src = await waitDataSrc(page);
    while (got < quota && saved < TOTAL) {
      const b64 = src.split(",", 2)[1];
      const buf = Buffer.from(b64, "base64");
      const h = md5(buf);
      if (!seen.has(h)) {
        seen.add(h);
        const name = `w${id}_${String(got).padStart(4, "0")}.png`;
        fs.writeFileSync(path.join(OUT, name), buf);
        manifest.push({ file: name });
        got++; saved++;
        if (saved % 50 === 0) process.stdout.write(`saved ${saved}/${TOTAL}\n`);
      }
      // rotate the session periodically to dodge stale-refresh throttling
      const prev = src;
      try {
        if (++sinceNav >= 30) { sinceNav = 0; throw new Error("rotate"); }
        await page.click(REFRESH_ID);
        src = await waitNewSrc(page, prev);
      } catch {
        await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});
        src = await waitDataSrc(page).catch(() => prev);
        sinceNav = 0;
      }
      await sleep(120);
    }
  } catch (e) {
    process.stdout.write(`worker ${id} error: ${String(e.message).split("\n")[0]}\n`);
  } finally {
    await ctx.close().catch(() => {});
  }
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const per = Math.ceil(TOTAL / WORKERS);
await Promise.all(Array.from({ length: WORKERS }, (_, i) => worker(browser, i, per)));
await browser.close();
fs.writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2));
console.log(`\nDone. ${manifest.length} unique captchas in ${OUT}`);
