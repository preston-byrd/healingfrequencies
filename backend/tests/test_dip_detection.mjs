// Node-runnable sanity test for the dip-detection algorithm in
// `frontend/src/lib/harmonicBlueprintEngine.js`. Run with:
//   node backend/tests/test_dip_detection.mjs
//
// The frontend module imports browser APIs (AudioBuffer), so we mirror
// ONLY the dip detection here — kept in sync manually.

function findDips(spectrum, winBins, depthDb) {
  const raw = [];
  const w = Math.max(1, winBins);
  for (let i = w; i < spectrum.length - w; i++) {
    const d = spectrum[i].db;
    let isLocalMin = true;
    for (let k = 1; k <= 2 && isLocalMin; k++) {
      if (spectrum[i - k].db < d || spectrum[i + k].db < d) isLocalMin = false;
    }
    if (!isLocalMin) continue;
    const shoulderAvg = (spectrum[i - w].db + spectrum[i + w].db) / 2;
    if (d < shoulderAvg - depthDb) {
      raw.push({ ...spectrum[i], depth: shoulderAvg - d });
    }
  }
  raw.sort((a, b) => b.depth - a.depth);
  const picked = [];
  for (const p of raw) {
    if (picked.every((q) => Math.abs(q.hz - p.hz) > 60)) picked.push(p);
    if (picked.length >= 4) break;
  }
  return picked;
}

function detect(spectrum) {
  let dips = findDips(spectrum, 6, 5);
  if (dips.length < 2) {
    const relaxed = findDips(spectrum, 10, 3);
    for (const r of relaxed) {
      if (dips.every((q) => Math.abs(q.hz - r.hz) > 60)) dips.push(r);
      if (dips.length >= 4) break;
    }
  }
  return dips;
}

function makeSpectrum(fn) {
  const s = [];
  for (let hz = 60; hz <= 4000; hz += 11.7) {
    s.push({ hz: +hz.toFixed(1), db: +fn(hz).toFixed(2) });
  }
  return s;
}

const cases = [
  {
    name: 'flat baseline + two clear notches at 500 & 1500 Hz',
    fn: (hz) => -12
      - 6 * Math.exp(-Math.pow((hz - 500) / 40, 2))
      - 8 * Math.exp(-Math.pow((hz - 1500) / 60, 2)),
    expectHz: [500, 1500],
  },
  {
    name: 'sloped voice baseline + notches at 500 & 1500 Hz',
    fn: (hz) => -5 + (hz - 60) / (4000 - 60) * -20
      - 6 * Math.exp(-Math.pow((hz - 500) / 40, 2))
      - 8 * Math.exp(-Math.pow((hz - 1500) / 60, 2)),
    expectHz: [500, 1500],
  },
  {
    name: 'realistic voice: peaks at 800/2500, notches at 350/1200',
    fn: (hz) => -25
      + 20 * Math.exp(-Math.pow((hz - 800) / 400, 2))
      + 12 * Math.exp(-Math.pow((hz - 2500) / 300, 2))
      - 5 * Math.exp(-Math.pow((hz - 350) / 30, 2))
      - 6 * Math.exp(-Math.pow((hz - 1200) / 40, 2)),
    expectHz: [350, 1200],
  },
  {
    name: 'smooth linear slope — MUST return NONE',
    fn: (hz) => -5 + (hz - 60) / (4000 - 60) * -20,
    expectHz: [],
  },
  {
    name: 'noisy wobble ±0.3 dB — MUST return NONE',
    fn: (_) => -15 + (Math.random() - 0.5) * 0.6,
    expectHz: [],
  },
];

let passed = 0, failed = 0;
for (const c of cases) {
  const dips = detect(makeSpectrum(c.fn));
  const got = dips.map((d) => Math.round(d.hz));
  const withinTolerance = (want, list) =>
    list.some((g) => Math.abs(g - want) <= 80);
  const allExpected = c.expectHz.every((h) => withinTolerance(h, got));
  const noExtras = c.expectHz.length === 0 ? got.length === 0 : true;
  const ok = allExpected && (c.expectHz.length > 0 || noExtras);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}`);
  console.log(`      expected≈${JSON.stringify(c.expectHz)} got=${JSON.stringify(got)}`);
  if (ok) passed++; else failed++;
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
