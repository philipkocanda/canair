/* Explorer data view: the discovery tools.
 *
 *  - heat-strip: every varying signal of the session as one coloured row, similar rows adjacent,
 *    doubling as a session minimap and a brush for the plot's time range
 *  - related signals: what follows a chosen signal, at which delay
 *  - what changed: which signals reacted inside the zoomed window
 *
 * The maths is in analysis.js (unit-tested); this file only arranges it on screen.
 * Plan: plans/2026-10-22-explorer-capture-plots.md
 */
(function () {
  "use strict";

  var X = window.canairExplorer, A = window.CanairAnalysis, P = window.canairPlot;
  if (!X || !A || !P) return;
  var el = X.el, fill = X.fill, tray = X.tray;

  var BINS = 600, GUTTER = 6, TOP_N = 12, MAX_LAG_S = 30, DELTA_S = 5;

  /* ------------------------------------------------------- worker or inline */

  var worker = null, pending = {}, nextId = 1;
  function runDirect(fn, args) {
    return new Promise(function (resolve, reject) {
      setTimeout(function () { try { resolve(A[fn].apply(null, args)); } catch (e) { reject(e); } }, 0);
    });
  }
  function analyze(fn, args) {
    if (worker === null) {
      try {
        worker = new Worker(new URL("worker.js", X.SCRIPT_URL));
        worker.onmessage = function (e) {
          var job = pending[e.data.id];
          delete pending[e.data.id];
          if (job) { if (e.data.error) job.reject(new Error(e.data.error)); else job.resolve(e.data.result); }
        };
        worker.onerror = function () {
          worker = false;
          var jobs = pending; pending = {};
          Object.keys(jobs).forEach(function (id) { runDirect(jobs[id].fn, jobs[id].args).then(jobs[id].resolve, jobs[id].reject); });
        };
      } catch (e) { worker = false; }
    }
    if (!worker) return runDirect(fn, args);
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { fn: fn, args: args, resolve: resolve, reject: reject };
      worker.postMessage({ id: id, fn: fn, args: args });
    });
  }

  function forgetOnFailure(store, key, promise) {
    return promise.catch(function (err) { delete store[key]; throw err; });
  }

  /* ------------------------------------------------------------------ grids */

  function varyingKeys(S) {
    return Object.keys(S.signals).filter(function (k) { return S.signals[k].varies; });
  }
  function grids(S) {
    if (!S.cache) S.cache = {};
    if (!S.grids) {
      S.grids = {};
      varyingKeys(S).forEach(function (k) {
        var s = S.signals[k];
        S.grids[k] = A.resample(s.t, s.v, 0, S.step, S.n, s.hold);
      });
    }
    return S.grids;
  }
  /** Binned, normalized rows in cluster order, computed once per session. */
  function heatData(S) {
    if (!S.heat) {
      var g = grids(S), rows = {};
      Object.keys(g).forEach(function (k) { rows[k] = A.normalize(A.binMeans(g[k], BINS)); });
      S.heat = { rows: rows, order: A.clusterOrder(rows) };
    }
    return S.heat;
  }

  /* -------------------------------------------------------------- heat-strip */

  function ramp(x) { return "hsl(" + Math.round(250 - 200 * x) + ",70%," + Math.round(22 + 38 * x) + "%)"; }

  var HEAT_MAX_H = 420, TICK_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 14400];

  /** Where the strip's time axis sits: under the plot's own plotting area when there is one. */
  function heatGeometry(V, wrap) {
    var w = wrap.getBoundingClientRect(), u = V.plots && V.plots[0];
    if (u && u.over && u.over.getBoundingClientRect().width) {
      var o = u.over.getBoundingClientRect();
      return { width: Math.round(w.width), x0: Math.round(o.left - w.left), plotW: Math.round(o.width) };
    }
    return { width: Math.round(w.width), x0: GUTTER + 2, plotW: Math.round(w.width) - GUTTER - 4 };
  }

  function heat(V) {
    var S = V.S;
    var count = varyingKeys(S).length, constant = Object.keys(S.signals).length - count;
    var title = function () {
      return el("h4", {}, "Session overview ", el("span", { class: "cx-muted" }, count + " signals that changed, one row each, similar rows " +
        "side by side; time runs left to right" + (constant ? " · " + constant + " constant signals hidden" : "")));
    };
    if (!count) { fill(V.heat, title(), el("p", { class: "cx-muted" }, "Nothing in this session changed value.")); return; }
    fill(V.heat, title(), el("p", { class: "cx-muted" }, "Computing…"));
    setTimeout(function () {
      if (V.S !== S || !V.heat) return;
      var h = heatData(S), n = h.order.length, dur = S.dur / 1000;
      var rh = Math.max(2, Math.min(6, Math.floor(HEAT_MAX_H / n)));
      var canvas = el("canvas", { class: "cx-heatcanvas", role: "img",
        "aria-label": "Overview of " + n + " signals over the session; each row is one signal, colour is its value from low (dark blue) to high (yellow)" });
      var rowMark = el("div", { class: "cx-heatrow", hidden: true });
      var brush = el("div", { class: "cx-brush", hidden: true });
      var tip = el("div", { class: "cx-heattip", hidden: true });
      var axis = el("div", { class: "cx-heataxis" });
      var wrap = el("div", { class: "cx-heatwrap" }, canvas, rowMark, brush, tip);
      fill(V.heat, title(), wrap, axis,
        el("p", { class: "cx-muted cx-heathelp" }, P.touch
          ? "Tap a row to see the signal and plot it (again to remove); drag sideways to zoom, reset zoom below to undo."
          : "Hover for a signal's value; click a row to plot it (again to remove); drag across time to zoom; double-click to reset."));

      var g = heatGeometry(V, wrap), dpr = devicePixelRatio || 1, height = n * rh;
      canvas.width = Math.round(g.width * dpr); canvas.height = Math.round(height * dpr);
      canvas.style.height = height + "px";
      var ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      var cellW = g.plotW / BINS;
      h.order.forEach(function (k, row) {
        var cells = h.rows[k];
        ctx.fillStyle = S.signals[k].verified ? "#59a14f" : "#f28e2b";
        ctx.fillRect(g.x0 - GUTTER, row * rh, GUTTER - 2, rh);
        for (var b = 0; b < BINS; b++) {
          if (cells[b] !== cells[b]) continue;
          ctx.fillStyle = ramp(cells[b]);
          ctx.fillRect(g.x0 + b * cellW, row * rh, cellW + 0.6, rh);
        }
      });
      drawAxis(axis, g, S, dur);
      V.heatEl = { brush: brush, geom: g, wrap: wrap };
      showBrush(V);

      function timeAt(ev) {
        var x = ev.clientX - canvas.getBoundingClientRect().left;
        return Math.max(0, Math.min(1, (x - g.x0) / g.plotW)) * dur;
      }
      function rowAt(ev) {
        var y = ev.clientY - canvas.getBoundingClientRect().top;
        var i = Math.floor(y / rh);
        return i >= 0 && i < n ? i : -1;
      }
      var down = null, tipTimer = 0;
      canvas.addEventListener("pointerdown", function (ev) {
        down = { x: ev.clientX, t: timeAt(ev) };
        window.addEventListener("pointerup", up, { once: true });
        if (ev.pointerType !== "mouse") hover(ev);
      });
      canvas.addEventListener("pointermove", hover);
      function hover(ev) {
        clearTimeout(tipTimer);
        var i = rowAt(ev), k = h.order[i], s = k && S.signals[k], t = timeAt(ev);
        if (s) {
          var v = P.valueAt(s, t * 1000);
          // Not fill(): this runs per pointer move, and the tooltip floats, so it cannot move the page.
          tip.replaceChildren(el("div", {}, el("strong", {}, s.name), " ", el("span", { class: "cx-muted" }, s.ecu + ":" + s.pid),
            el("br", {}), P.fmt(v) + (s.unit && v === v ? " " + s.unit : ""), el("span", { class: "cx-muted" }, " at " + P.clock(S.entry.start, t)),
            s.verified ? null : el("div", { class: "cx-muted" }, "unverified signal"),
            el("div", { class: "cx-muted" }, (tray.has(k) ? "in the plot · " + (P.touch ? "tap" : "click") + " to remove" : (P.touch ? "tap" : "click") + " to plot"))));
          var r = wrap.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
          tip.hidden = false;
          tip.style.left = Math.max(0, Math.min(x + 14, r.width - tip.offsetWidth - 4)) + "px";
          tip.style.top = (y + 14 + tip.offsetHeight > r.height + 40 ? y - tip.offsetHeight - 8 : y + 14) + "px";
          rowMark.hidden = false;
          rowMark.style.top = i * rh + "px"; rowMark.style.height = rh + "px";
          rowMark.style.left = g.x0 + "px"; rowMark.style.width = g.plotW + "px";
        } else { tip.hidden = true; rowMark.hidden = true; }
        if (down && Math.abs(ev.clientX - down.x) > 4) {
          var a = Math.min(down.t, t), b = Math.max(down.t, t);
          brush.hidden = false;
          brush.style.left = g.x0 + a / dur * g.plotW + "px";
          brush.style.width = (b - a) / dur * g.plotW + "px";
        }
      }
      canvas.addEventListener("pointerleave", function (ev) {
        // A finger "leaves" as it lifts, so keep a tapped row's tooltip up long enough to read.
        clearTimeout(tipTimer);
        tipTimer = setTimeout(function () { tip.hidden = true; rowMark.hidden = true; }, ev.pointerType === "mouse" ? 0 : 2500);
      });
      function up(ev) {
        if (!down) return;
        var d = down; down = null;
        if (Math.abs(ev.clientX - d.x) > 4) P.setRange(Math.min(d.t, timeAt(ev)), Math.max(d.t, timeAt(ev)));
        else if (ev.target === canvas) { var i = rowAt(ev); if (i >= 0) tray.toggle(h.order[i]); }
        showBrush(V);
      }
      canvas.addEventListener("dblclick", function () { P.setRange(0, dur); });
    }, 0);
  }

  /** Clock-time ticks under the strip, so it reads as a timeline. */
  function drawAxis(axis, g, S, dur) {
    var step = TICK_STEPS.filter(function (s) { return dur / s <= 8; })[0] || TICK_STEPS[TICK_STEPS.length - 1];
    var ticks = [];
    for (var t = 0; t <= dur; t += step) ticks.push(t);
    axis.style.marginLeft = g.x0 + "px";
    axis.style.width = g.plotW + "px";
    axis.replaceChildren.apply(axis, ticks.map(function (t) {
      return el("span", { style: "left:" + (t / dur * 100) + "%" }, P.clock(S.entry.start, t).slice(0, 5));
    }));
  }

  /** Redraw the strip when the plot's plotting area moved (a plot appeared or the width changed). */
  function realign(V) {
    if (!V.S || !V.heatEl || !V.heatEl.wrap.isConnected) return;
    var g = heatGeometry(V, V.heatEl.wrap), o = V.heatEl.geom;
    if (Math.abs(g.x0 - o.x0) > 2 || Math.abs(g.plotW - o.plotW) > 2) heat(V);
  }

  function showBrush(V) {
    if (!V.heatEl) return;
    var dur = V.S.dur / 1000, g = V.heatEl.geom, brush = V.heatEl.brush;
    brush.hidden = !V.range;
    if (V.range) {
      brush.style.left = g.x0 + V.range[0] / dur * g.plotW + "px";
      brush.style.width = (V.range[1] - V.range[0]) / dur * g.plotW + "px";
    }
  }

  /* ------------------------------------------------------------ side panels */

  function sparkline(S, key) {
    var rows = S.heat && S.heat.rows[key], c = el("canvas", { class: "cx-spark", width: 90, height: 22 });
    if (!rows) return c;
    var ctx = c.getContext("2d");
    ctx.strokeStyle = "#888"; ctx.lineWidth = 1; ctx.beginPath();
    var started = false;
    for (var b = 0; b < BINS; b += 5) {
      var v = rows[b];
      if (v !== v) { started = false; continue; }
      var x = b / BINS * 90, y = 20 - v * 18;
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    }
    ctx.stroke();
    return c;
  }
  function addButton(key) {
    return el("button", { type: "button", class: "cx-plus" + (tray.has(key) ? " cx-on" : ""), "data-id": key, title: "Add to the plot",
      onclick: function () { tray.toggle(key); } }, tray.has(key) ? "✓" : "＋");
  }
  function signalCell(S, key) {
    var s = S.signals[key];
    return el("div", {}, el("strong", {}, s.name), " ", el("span", { class: "cx-muted" }, s.ecu + ":" + s.pid + (s.unit ? " · " + s.unit : "")),
      s.verified ? null : [" ", el("span", { class: "cx-badge cx-warn" }, "unverified")]);
  }

  function lagText(S, lag) {
    var sec = Math.abs(lag) * S.step / 1000;
    return lag === 0 ? "in sync" : (lag > 0 ? "follows by " : "leads by ") + (sec >= 90 ? Math.round(sec / 60) + " min" : Math.round(sec) + " s");
  }

  function relatedPanel(V, body) {
    var S = V.S, keys = tray.ids.filter(function (k) { return S.signals[k] && S.signals[k].varies; });
    grids(S);
    var focus = V.focus && S.signals[V.focus] && S.signals[V.focus].varies ? V.focus : keys[0];
    var mode = V.relMode || "delta";
    var toggle = el("div", { class: "cx-seg" }, [["delta", "Changes (recommended)"], ["level", "Levels"]].map(function (m) {
      return el("button", { type: "button", class: "cx-segbtn" + (mode === m[0] ? " cx-active" : ""), onclick: function () { V.relMode = m[0]; panels(V); } }, m[1]);
    }));
    if (!focus) {
      fill(body, el("p", { class: "cx-muted" }, "Put a varying signal in the plot, then click its name in the table to see which other signals follow it."));
      return;
    }
    var key = [S.entry.id, focus, mode].join("|");
    fill(body, el("p", {}, "Signals that move with ", el("strong", {}, S.signals[focus].name), ": ", toggle),
      el("p", { class: "cx-muted" }, mode === "delta"
        ? "Compares how signals change over " + DELTA_S + " s, so two things that both drift upward over a session do not look related."
        : "Compares raw levels. Anything that rises or falls over the whole session will match; treat strong hits with suspicion."),
      el("p", { class: "cx-muted" }, "Searching…"));
    V.relKey = key;
    var g = grids(S), others = {};
    Object.keys(g).forEach(function (k) { if (k !== focus) others[k] = g[k]; });
    var maxLag = Math.max(1, Math.round(MAX_LAG_S * 1000 / S.step));
    var job = S.cache[key] || (S.cache[key] = forgetOnFailure(S.cache, key, analyze("rankRelated", [g[focus], others,
      { mode: mode, maxLag: maxLag, deltaSteps: Math.max(1, Math.round(DELTA_S * 1000 / S.step)) }])));
    job.then(function (res) {
      if (V.relKey !== key || !V.S || V.S !== S) return;
      heatData(S);
      var rows = res.slice(0, TOP_N);
      fill(body, body.children[0], body.children[1],
        rows.length ? el("div", { class: "cx-scroll" }, el("table", { class: "cx-table cx-cardtable cx-reltable" }, el("tbody", {}, rows.map(function (r) {
          return el("tr", {}, el("td", { class: "cx-c-plus" }, addButton(r.key)), el("td", { class: "cx-c-sig" }, signalCell(S, r.key)),
            el("td", { class: "cx-num cx-c-score" }, (r.r > 0 ? "+" : "") + r.r.toFixed(2)),
            el("td", { class: "cx-muted cx-c-lag" }, lagText(S, r.lag)), el("td", { class: "cx-muted cx-c-n" }, "n=" + r.n),
            el("td", {}, r.mirror ? el("span", { class: "cx-badge cx-plain", title: "Matches at almost exactly 1.0: probably the same value, rescaled" }, "mirror") : null),
            el("td", { class: "cx-c-spark" }, sparkline(S, r.key)));
        })))) : el("p", { class: "cx-muted" }, "Nothing else in this session moved enough to compare."),
        el("p", { class: "cx-muted" }, "A strong match is a lead, not an explanation: check that it makes physical sense, then look at the raw bytes."));
    }, function (err) { fill(body, el("p", { class: "cx-muted" }, "Analysis failed: " + err.message)); });
  }

  function changedPanel(V, body) {
    var S = V.S;
    grids(S);
    if (!V.range) {
      fill(body, el("p", {}, "Zoom into a moment, by dragging on the plot or on the overview above, and this lists the signals that reacted inside it."),
        el("p", { class: "cx-muted" }, "Try the instant charging starts, a relay clicks, or the pedal goes down."));
      return;
    }
    var key = [S.entry.id, V.range[0].toFixed(1), V.range[1].toFixed(1)].join("|");
    V.chgKey = key;
    var i0 = Math.max(0, Math.floor(V.range[0] * 1000 / S.step)), i1 = Math.min(S.n - 1, Math.ceil(V.range[1] * 1000 / S.step));
    fill(body, el("p", {}, "What changed between ", el("strong", {}, P.clock(S.entry.start, V.range[0])), " and ",
      el("strong", {}, P.clock(S.entry.start, V.range[1])), " (" + P.duration((V.range[1] - V.range[0]) * 1000) + ")?"),
      el("p", { class: "cx-muted" }, "Searching…"));
    var job = S.cache[key] || (S.cache[key] = forgetOnFailure(S.cache, key, analyze("changeScores", [grids(S), i0, i1])));
    job.then(function (res) {
      if (V.chgKey !== key || V.S !== S) return;
      var rows = res.slice(0, TOP_N);
      fill(body, body.children[0],
        rows.length ? el("div", { class: "cx-scroll" }, el("table", { class: "cx-table cx-cardtable" }, el("tbody", {}, rows.map(function (r) {
          return el("tr", {}, el("td", { class: "cx-c-plus" }, addButton(r.key)), el("td", { class: "cx-c-sig" }, signalCell(S, r.key)),
            el("td", {}, el("span", { class: "cx-badge " + (r.kind === "flip" ? "cx-ok" : "cx-plain") }, r.kind === "flip" ? "flag flipped" : r.kind === "level" ? "level shift" : "more active")),
            el("td", { class: "cx-muted" }, "inside " + P.fmt(r.inside) + " · outside " + P.fmt(r.outside)),
            el("td", { class: "cx-num cx-c-score" }, r.score.toFixed(1) + " σ"));
        })))) : el("p", { class: "cx-muted" }, "No signal stands out in this window."),
        el("p", { class: "cx-muted" }, "Scores are in units of each signal's own spread over the session. A window with few samples exaggerates them."));
    }, function (err) { fill(body, el("p", { class: "cx-muted" }, "Analysis failed: " + err.message)); });
  }

  function panels(V) {
    if (!V.S || !V.discover) return;
    var tab = V.dtab || "related";
    var body = el("div", { class: "cx-discover-body" });
    fill(V.discover, 
      el("div", { class: "cx-tabs" }, [["related", "Related signals"], ["changed", "What changed here?"]].map(function (t) {
        return el("button", { type: "button", class: "cx-tab" + (tab === t[0] ? " cx-active" : ""), role: "tab", "aria-selected": tab === t[0] ? "true" : "false",
          onclick: function () { V.dtab = t[0]; panels(V); } }, t[1]);
      })), body);
    (tab === "related" ? relatedPanel : changedPanel)(V, body);
  }

  window.canairDiscover = {
    heat: heat,
    panels: panels,
    realign: realign,
    rangeChanged: function (V) { showBrush(V); if (V.dtab === "changed") panels(V); },
  };
})();
