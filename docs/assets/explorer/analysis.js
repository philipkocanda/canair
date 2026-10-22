/* Pure analysis kernels for the explorer's data view. No DOM, no network.
 *
 * Loaded as a plain script in the page (window.CanairAnalysis), inside a Web Worker via
 * importScripts, and by `node --test` (tests/js/analysis.test.js), so the logic that can
 * mislead a reader is exercised without a browser.
 *
 * Conventions: a "grid" is a Float64Array of values at a fixed step, NaN where unknown.
 * Lag is in grid steps; a POSITIVE lag means the other series follows the focus series
 * (other[i + lag] resembles focus[i]).
 * Plan: plans/2026-10-22-explorer-capture-plots.md
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CanairAnalysis = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MIRROR_R = 0.999;

  /** Median gap between samples; used to decide how long a held value stays valid. */
  function medianGap(t) {
    if (t.length < 2) return 1000;
    var gaps = [];
    for (var i = 1; i < t.length; i++) gaps.push(t[i] - t[i - 1]);
    gaps.sort(function (a, b) { return a - b; });
    return Math.max(gaps[gaps.length >> 1], 1);
  }

  /**
   * Hold each sample until the next one (zero-order hold), up to `holdMs`.
   * t: ascending sample times (ms), v: values (NaN = no value). Grid time i is startMs + i*stepMs.
   */
  function resample(t, v, startMs, stepMs, n, holdMs) {
    var out = new Float64Array(n).fill(NaN);
    var j = -1;
    for (var i = 0; i < n; i++) {
      var g = startMs + i * stepMs;
      while (j + 1 < t.length && t[j + 1] <= g) j++;
      if (j >= 0 && g - t[j] <= holdMs) out[i] = v[j];
    }
    return out;
  }

  function summary(a) {
    var n = 0, sum = 0, min = Infinity, max = -Infinity, i, x;
    for (i = 0; i < a.length; i++) {
      x = a[i];
      if (x !== x) continue;
      n++; sum += x;
      if (x < min) min = x;
      if (x > max) max = x;
    }
    if (!n) return { n: 0, mean: NaN, sd: 0, min: NaN, max: NaN };
    var mean = sum / n, ss = 0;
    for (i = 0; i < a.length; i++) { x = a[i]; if (x === x) ss += (x - mean) * (x - mean); }
    return { n: n, mean: mean, sd: Math.sqrt(ss / n), min: min, max: max };
  }

  /** First differences over `k` steps: out[i] = a[i] - a[i-k]. */
  function diff(a, k) {
    var out = new Float64Array(a.length).fill(NaN);
    for (var i = k; i < a.length; i++) out[i] = a[i] - a[i - k];
    return out;
  }

  /** Pearson r of x[i] against y[i+lag] over the pairs where both exist. */
  function pearson(x, y, lag) {
    var n = 0, sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
    var from = Math.max(0, -lag), to = Math.min(x.length, y.length - lag);
    for (var i = from; i < to; i++) {
      var a = x[i], b = y[i + lag];
      if (a !== a || b !== b) continue;
      n++; sx += a; sy += b; sxx += a * a; syy += b * b; sxy += a * b;
    }
    if (n < 3) return { r: NaN, n: n };
    var vx = sxx - sx * sx / n, vy = syy - sy * sy / n;
    if (vx <= 1e-12 || vy <= 1e-12) return { r: NaN, n: n };
    return { r: (sxy - sx * sy / n) / Math.sqrt(vx * vy), n: n };
  }

  /**
   * Rank `others` by how closely they follow `focus`.
   * opts: {mode: "delta"|"level", deltaSteps, maxLag, lagStep, minN}.
   * Delta mode correlates changes, not levels: two quantities that merely both drift up over a
   * session correlate perfectly in level mode and mean nothing.
   */
  function rankRelated(focus, others, opts) {
    opts = opts || {};
    var delta = (opts.mode || "delta") === "delta";
    var k = opts.deltaSteps || 5;
    var maxLag = opts.maxLag === undefined ? 30 : opts.maxLag;
    var lagStep = opts.lagStep || 1;
    var minN = opts.minN || 30;
    var f = delta ? diff(focus, k) : focus;
    var results = [];
    Object.keys(others).forEach(function (key) {
      var o = delta ? diff(others[key], k) : others[key];
      var r0 = pearson(f, o, 0);
      if (r0.n < minN) return;
      var best = { r: r0.r, lag: 0, n: r0.n };
      for (var lag = -maxLag; lag <= maxLag; lag += lagStep) {
        if (!lag) continue;
        var p = pearson(f, o, lag);
        if (p.n >= minN && Math.abs(p.r) > Math.abs(best.r || 0) + 1e-9) best = { r: p.r, lag: lag, n: p.n };
      }
      if (best.r !== best.r) return;
      results.push({
        key: key, r0: r0.r, r: best.r, lag: best.lag, n: best.n,
        mirror: !delta && Math.abs(r0.r) >= MIRROR_R,
      });
    });
    results.sort(function (a, b) { return Math.abs(b.r) - Math.abs(a.r); });
    return results;
  }

  function modeOf(values) {
    var counts = {}, best = NaN, bestN = 0;
    values.forEach(function (v) { counts[v] = (counts[v] || 0) + 1; if (counts[v] > bestN) { bestN = counts[v]; best = v; } });
    return best;
  }

  /**
   * Score how much each signal reacts inside the window [i0, i1] (grid indices) compared with the
   * rest of the session, in units of the signal's own session-wide spread. A signal that has two
   * values only (a flag) is reported as a flip when its most common value differs inside.
   */
  function changeScores(grids, i0, i1, minPoints) {
    minPoints = minPoints || 3;
    var out = [];
    Object.keys(grids).forEach(function (key) {
      var g = grids[key], inside = [], outside = [];
      for (var i = 0; i < g.length; i++) {
        if (g[i] !== g[i]) continue;
        (i >= i0 && i <= i1 ? inside : outside).push(g[i]);
      }
      if (inside.length < minPoints || outside.length < minPoints) return;
      var all = summary(g);
      if (all.sd <= 1e-12) return;
      var a = summary(inside), b = summary(outside);
      var level = Math.abs(a.mean - b.mean) / all.sd;
      var activity = Math.abs(a.sd - b.sd) / all.sd;
      var distinct = {};
      for (var q = 0; q < g.length; q++) if (g[q] === g[q]) distinct[g[q]] = 1;
      var isFlag = Object.keys(distinct).length <= 2;
      var flip = isFlag && modeOf(inside) !== modeOf(outside);
      out.push({
        key: key, score: Math.max(level, activity) + (flip ? 1 : 0),
        kind: flip ? "flip" : (level >= activity ? "level" : "activity"),
        inside: a.mean, outside: b.mean,
      });
    });
    out.sort(function (x, y) { return y.score - x.score; });
    return out;
  }

  /** Mean of a series in `bins` equal slices (NaN slices stay NaN); the heat-strip's resolution. */
  function binMeans(a, bins) {
    var out = new Float64Array(bins).fill(NaN);
    for (var b = 0; b < bins; b++) {
      var from = Math.floor(b * a.length / bins), to = Math.max(from + 1, Math.floor((b + 1) * a.length / bins));
      var sum = 0, n = 0;
      for (var i = from; i < to && i < a.length; i++) if (a[i] === a[i]) { sum += a[i]; n++; }
      if (n) out[b] = sum / n;
    }
    return out;
  }

  /** Rescale to 0..1 by the series' own min and max; a constant series becomes all 0.5. */
  function normalize(a) {
    var s = summary(a), out = new Float64Array(a.length);
    for (var i = 0; i < a.length; i++) out[i] = a[i] !== a[i] ? NaN : (s.max > s.min ? (a[i] - s.min) / (s.max - s.min) : 0.5);
    return out;
  }

  /**
   * Order rows so similar ones sit together: average-linkage agglomerative clustering on 1-|r|,
   * returning the leaf order. rows: {key: Float64Array}. Returns an array of keys.
   */
  function clusterOrder(rows) {
    var keys = Object.keys(rows), n = keys.length;
    if (n < 3) return keys;
    var dist = [];
    for (var i = 0; i < n; i++) {
      dist.push(new Float64Array(n));
      for (var j = 0; j < i; j++) {
        var r = pearson(rows[keys[i]], rows[keys[j]], 0).r;
        dist[i][j] = dist[j][i] = r === r ? 1 - Math.abs(r) : 1;
      }
    }
    var clusters = keys.map(function (_, idx) { return { members: [idx] }; });
    while (clusters.length > 1) {
      var bi = 0, bj = 1, bd = Infinity;
      for (var a = 0; a < clusters.length; a++) {
        for (var b = a + 1; b < clusters.length; b++) {
          var s = 0, ca = clusters[a].members, cb = clusters[b].members;
          for (var x = 0; x < ca.length; x++) for (var y = 0; y < cb.length; y++) s += dist[ca[x]][cb[y]];
          s /= ca.length * cb.length;
          if (s < bd) { bd = s; bi = a; bj = b; }
        }
      }
      clusters[bi] = { members: clusters[bi].members.concat(clusters[bj].members) };
      clusters.splice(bj, 1);
    }
    return clusters[0].members.map(function (idx) { return keys[idx]; });
  }

  return {
    MIRROR_R: MIRROR_R, medianGap: medianGap, resample: resample, summary: summary, diff: diff,
    pearson: pearson, rankRelated: rankRelated, changeScores: changeScores, binMeans: binMeans,
    normalize: normalize, clusterOrder: clusterOrder,
  };
});
