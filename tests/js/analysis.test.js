// Run with: node --test tests/js/   (no dependencies)
const test = require("node:test");
const assert = require("node:assert/strict");
const A = require("../../docs/assets/explorer/analysis.js");

function lcg(seed) {
  let s = seed;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296) - 0.5;
}
const series = (n, f) => Float64Array.from({ length: n }, (_, i) => f(i));

test("resample holds the last value and lets it expire", () => {
  const g = A.resample([0, 2000], [1, 5], 0, 1000, 6, 1500);
  assert.deepEqual(Array.from(g).map((x) => (x !== x ? null : x)), [1, 1, 5, 5, null, null]);
});

test("a positive lag means the other series follows the focus", () => {
  const rnd = lcg(1);
  const x = series(400, () => rnd());
  const y = series(400, (i) => (i >= 3 ? x[i - 3] : NaN));
  const [hit] = A.rankRelated(x, { y }, { mode: "level", maxLag: 10 });
  assert.equal(hit.lag, 3);
  assert.ok(hit.r > 0.99);
});

test("delta mode ignores two signals that merely drift in the same direction", () => {
  const a = lcg(2), b = lcg(3);
  const x = series(600, (i) => i + 5 * a());
  const y = series(600, (i) => i + 5 * b());
  const level = A.rankRelated(x, { y }, { mode: "level", maxLag: 0 })[0];
  const delta = A.rankRelated(x, { y }, { mode: "delta", maxLag: 0 })[0];
  assert.ok(level.r > 0.99, "levels look perfectly related");
  assert.ok(Math.abs(delta.r) < 0.3, "their changes are not");
});

test("an exact linear copy is flagged as a mirror, not a discovery", () => {
  const rnd = lcg(4);
  const x = series(200, () => rnd());
  const y = series(200, (i) => 2 * x[i] + 1);
  assert.equal(A.rankRelated(x, { y }, { mode: "level", maxLag: 0 })[0].mirror, true);
});

test("constant signals and too-short overlaps are skipped", () => {
  const rnd = lcg(5);
  const x = series(100, () => rnd());
  const flat = series(100, () => 7);
  const tiny = series(100, (i) => (i < 5 ? x[i] : NaN));
  assert.deepEqual(A.rankRelated(x, { flat, tiny }, { mode: "level", maxLag: 0 }), []);
});

test("changeScores ranks a flag that flips inside the window first", () => {
  const rnd = lcg(6);
  const noisy = series(300, () => rnd());
  const flag = series(300, (i) => (i >= 100 && i < 150 ? 1 : 0));
  const ramp = series(300, (i) => i / 300);
  const [top] = A.changeScores({ noisy, flag, ramp }, 100, 149);
  assert.equal(top.key, "flag");
  assert.equal(top.kind, "flip");
});

test("changeScores ignores constant signals", () => {
  assert.deepEqual(A.changeScores({ c: series(50, () => 3) }, 10, 20), []);
});

test("clusterOrder puts similar rows next to each other", () => {
  const rnd = lcg(7), other = lcg(8);
  const base = series(120, () => rnd());
  const rows = {
    a: base,
    z: series(120, () => other()),
    b: series(120, (i) => base[i] * -3 + 0.01 * other()),
  };
  const order = A.clusterOrder(rows);
  assert.ok(Math.abs(order.indexOf("a") - order.indexOf("b")) === 1);
});

test("binMeans and normalize keep gaps as gaps", () => {
  const bins = A.binMeans(Float64Array.from([1, 3, NaN, NaN, 5, 7]), 3);
  assert.deepEqual(Array.from(bins).map((x) => (x !== x ? null : x)), [2, null, 6]);
  const n = A.normalize(Float64Array.from([10, NaN, 20]));
  assert.equal(n[0], 0);
  assert.ok(n[1] !== n[1]);
  assert.equal(n[2], 1);
});
