#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const {pathToFileURL} = require("url");

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function browserCandidates(explicit) {
  const values = [
    explicit,
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.platform === "win32" && "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    process.platform === "win32" && "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    process.platform === "win32" && "C:/Program Files/Google/Chrome/Application/chrome.exe",
    process.platform === "darwin" && "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    process.platform === "darwin" && "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    process.platform === "linux" && "/usr/bin/google-chrome",
    process.platform === "linux" && "/usr/bin/chromium",
    process.platform === "linux" && "/usr/bin/chromium-browser",
  ];
  return [...new Set(values.filter(Boolean).map((value) => path.resolve(value)))];
}

async function launch(chromium, explicit) {
  const candidates = browserCandidates(explicit).filter((value) => fs.existsSync(value));
  const managed = chromium.executablePath();
  if (managed && fs.existsSync(managed)) candidates.unshift(managed);
  const errors = [];
  for (const executablePath of [...new Set(candidates)]) {
    try {
      return await chromium.launch({headless: true, executablePath});
    } catch (error) {
      errors.push(`${executablePath}: ${error.message.split("\n")[0]}`);
    }
  }
  try {
    return await chromium.launch({headless: true});
  } catch (error) {
    errors.push(`Playwright default: ${error.message.split("\n")[0]}`);
  }
  fail(`No usable Chromium, Edge, or Chrome browser was found. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE.\n${errors.join("\n")}`, 4);
}

async function main() {
  const [inputArg, outputArg, browserArg] = process.argv.slice(2);
  if (!inputArg || !outputArg) fail("Usage: capture_cards.cjs <deck.html> <output-dir> [browser-executable]", 2);
  const input = path.resolve(inputArg);
  const output = path.resolve(outputArg);
  if (!fs.existsSync(input)) fail(`Deck HTML does not exist: ${input}`, 2);
  fs.mkdirSync(output, {recursive: true});

  let chromium;
  try {
    ({chromium} = require("playwright"));
  } catch (error) {
    try {
      ({chromium} = require("playwright-core"));
    } catch (coreError) {
      fail("PNG rendering needs Node.js playwright-core (or Playwright). Install the optional dependency explicitly; the HTML deck remains usable.", 3);
    }
  }

  const browser = await launch(chromium, browserArg);
  try {
    const context = await browser.newContext({viewport: {width: 1220, height: 1580}, deviceScaleFactor: 1});
    const page = await context.newPage();
    await page.route(/^https?:\/\//, (route) => route.abort("blockedbyclient"));
    await page.goto(pathToFileURL(input).href, {waitUntil: "load"});
    await page.evaluate(() => document.fonts.ready);
    const cards = page.locator(".card[data-card-id]");
    const count = await cards.count();
    if (!count) fail(`No .card[data-card-id] elements found in ${input}`, 5);
    const rendered = [];
    for (let index = 0; index < count; index += 1) {
      const card = cards.nth(index);
      const result = await card.evaluate((element) => {
        const content = element.querySelector("[data-fit]");
        if (!content) return {ok: false, reason: "missing [data-fit] container"};
        let fit = 1;
        const layout = () => {
          const cardRect = element.getBoundingClientRect();
          const regions = [...element.querySelectorAll(":scope > .card-header, :scope > .card-body, :scope > .card-footer")];
          const outside = regions.find((region) => {
            const rect = region.getBoundingClientRect();
            return rect.left < cardRect.left - 1 || rect.right > cardRect.right + 1 || rect.top < cardRect.top - 1 || rect.bottom > cardRect.bottom + 1;
          });
          const clipped = regions.find((region) => region.scrollWidth > region.clientWidth + 1 || region.scrollHeight > region.clientHeight + 1);
          const pageNumber = element.querySelector(".page-number");
          const pageOk = pageNumber && /^(?:附 )?\d{2} \/ \d{2}$/.test(pageNumber.textContent.trim()) && pageNumber.scrollWidth <= pageNumber.clientWidth + 1;
          if (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1) return "card canvas overflows";
          if (outside) return `${outside.className} leaves the 1080x1440 canvas`;
          if (clipped) return `${clipped.className} is clipped`;
          if (!pageOk) return "page number is incomplete or clipped";
          return "";
        };
        element.style.setProperty("--fit", String(fit));
        while (layout() && fit > 0.68) {
          fit = Math.round((fit - 0.04) * 100) / 100;
          element.style.setProperty("--fit", String(fit));
        }
        const rect = element.getBoundingClientRect();
        const reason = layout();
        return {
          ok: !reason && Math.round(rect.width) === 1080 && Math.round(rect.height) === 1440,
          reason: reason || `unexpected canvas ${rect.width}x${rect.height}`,
          fit,
          cardId: element.dataset.cardId,
          role: element.dataset.role || "card",
        };
      });
      if (!result.ok) fail(`Card ${result.cardId || index + 1} cannot be rendered safely: ${result.reason}`, 6);
      const filename = `${String(index + 1).padStart(2, "0")}-${String(result.role).replace(/[^a-z0-9_-]+/gi, "-")}.png`;
      const buffer = await card.screenshot({path: path.join(output, filename), type: "png"});
      const width = buffer.readUInt32BE(16);
      const height = buffer.readUInt32BE(20);
      if (width !== 1080 || height !== 1440) fail(`Card ${result.cardId} rendered at ${width}x${height}, expected 1080x1440`, 7);
      rendered.push({card_id: result.cardId, role: result.role, filename, width, height, fit: result.fit});
    }
    process.stdout.write(`${JSON.stringify({input: path.basename(input), output: path.basename(output), cards: rendered}, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => fail(error.stack || error.message));
