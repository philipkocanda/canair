/* Explorer data view: pick a session, plot captured signals from any ECU together.
 *
 * Needs explorer.js (window.canairExplorer), uPlot and analysis.js. Decoding happens at docs build
 * time (canlib/explorer_series.py); this file only draws. The discovery panels live in
 * discover.js. Plan: plans/2026-10-22-explorer-capture-plots.md
 */
(function () {
  "use strict";

  var X = window.canairExplorer;
  var A = window.CanairAnalysis;
  if (!X || !A || !window.uPlot) return;
  var el = X.el, tray = X.tray;

  var PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#59a14f", "#b07aa1", "#76b7b2", "#edc948", "#ff9da7", "#9c755f", "#86bcb6"];
  var LANE_H = 170, OVERLAY_H = 340, RIBBON_ROW = 11, MAX_PER_LANE = 6, AXIS_W = 64, GRID_POINTS = 40000;
  var TIME_INCRS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400];

  var cache = { sessions: {}, docs: {} };
  var api = window.canairPlot = {};
  var V = null; // the mounted view, rebuilt when the host or profile changes

  /* ------------------------------------------------------------------ data */

  function fetchJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url.pathname.split("/").slice(-2).join("/") + ": HTTP " + r.status);
      return r.json();
    });
  }
  function loadSessions(name) {
    if (!cache.sessions[name]) cache.sessions[name] = fetchJson(new URL(name + "/sessions.json", X.DATA_URL));
    return cache.sessions[name];
  }
  function loadDoc(name, id) {
    var k = name + "/" + id;
    if (!cache.docs[k]) cache.docs[k] = fetchJson(new URL(name + "/s/" + id + ".json", X.DATA_URL));
    return cache.docs[k];
  }

  /** "ECU:PID:NAME" -> {unit, verified} from the definitions export. */
  function definitionIndex(data) {
    var out = {};
    data.ecus.forEach(function (e) {
      e.pids.forEach(function (p) {
        p.signals.forEach(function (s) { out[X.trayId(e.name, p.id, s.name)] = { unit: s.unit || "", verified: !!s.verified }; });
      });
    });
    return out;
  }

  /** Turn a session document into typed arrays, one record per signal. */
  function prepare(entry, doc, defs) {
    var runLength = entry.keep_mode === "changes";
    var signals = {};
    doc.streams.forEach(function (st) {
      var t = new Float64Array(st.t.length), acc = 0;
      for (var i = 0; i < st.t.length; i++) { acc += st.t[i]; t[i] = acc; }
      var hold = runLength ? Infinity : Math.max(10 * A.medianGap(t), 5000);
      Object.keys(st.sig).forEach(function (name) {
        var raw = st.sig[name], v = new Float64Array(t.length), varies = Array.isArray(raw);
        for (var j = 0; j < t.length; j++) {
          var x = varies ? raw[j] : raw;
          v[j] = x === null ? NaN : x;
        }
        var key = X.trayId(st.ecu, st.pid, name), d = defs[key] || {};
        signals[key] = { key: key, ecu: st.ecu, pid: st.pid, name: name, t: t, v: v, hold: hold, varies: varies, unit: d.unit || "", verified: !!d.verified };
      });
    });
    var step = Math.max(1000, Math.ceil(doc.dur / GRID_POINTS / 1000) * 1000);
    return { entry: entry, doc: doc, dur: doc.dur, signals: signals, step: step, n: Math.floor(doc.dur / step) + 1, spans: doc.spans || [], heat: null, grids: null };
  }

  function valueAt(sig, tMs) {
    var lo = 0, hi = sig.t.length - 1, idx = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (sig.t[mid] <= tMs) { idx = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return idx < 0 || tMs - sig.t[idx] > sig.hold ? NaN : sig.v[idx];
  }

  /** One shared time axis (seconds) for the chosen signals, each held to every instant. */
  function align(S, keys) {
    var all = [];
    keys.forEach(function (k) { all.push.apply(all, Array.prototype.slice.call(S.signals[k].t)); });
    all.sort(function (a, b) { return a - b; });
    var ts = all.filter(function (v, i) { return i === 0 || v !== all[i - 1]; });
    var ys = {};
    keys.forEach(function (k) {
      var sig = S.signals[k], out = new Array(ts.length), j = -1;
      for (var i = 0; i < ts.length; i++) {
        while (j + 1 < sig.t.length && sig.t[j + 1] <= ts[i]) j++;
        var ok = j >= 0 && ts[i] - sig.t[j] <= sig.hold && sig.v[j] === sig.v[j];
        out[i] = ok ? sig.v[j] : null;
      }
      ys[k] = out;
    });
    return { x: ts.map(function (v) { return v / 1000; }), ys: ys };
  }

  /* ------------------------------------------------------------- formatting */

  function fmt(v) {
    if (v === null || v !== v) return "—";
    var a = Math.abs(v);
    if (a >= 1000) return v.toFixed(0);
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    return a === 0 ? "0" : v.toPrecision(3);
  }
  function duration(ms) {
    var s = Math.round(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return h ? h + " h " + m + " min" : m ? m + " min " + (s % 60) + " s" : s + " s";
  }
  function clock(start, sec) {
    var p = start.split(":"), t = Math.round(+p[0] * 3600 + +p[1] * 60 + +p[2] + sec);
    t = ((t % 86400) + 86400) % 86400;
    return [Math.floor(t / 3600), Math.floor(t % 3600 / 60), t % 60].map(function (n) { return String(n).padStart(2, "0"); }).join(":");
  }
  function css(name) { return getComputedStyle(document.body).getPropertyValue(name).trim() || "#888"; }
  function hue(str) {
    var h = 0;
    for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
    return h % 360;
  }
  function colorOf(key) { var i = tray.ids.indexOf(key); return PALETTE[(i < 0 ? 0 : i) % PALETTE.length]; }

  /* ------------------------------------------------------------- the view */

  function mountView(host, data, ui) {
    V = {
      host: host, data: data, name: data.profile.name, ui: ui, defs: definitionIndex(data), sessions: null,
      id: "", S: null, token: 0, layout: "stack", range: null, pins: [], focus: "", plots: [], cells: {},
      filter: "", states: {}, rangeTimer: 0,
      list: el("div", { class: "cx-sessions" }), main: el("div", { class: "cx-session" }),
      renderList: renderList, renderMain: renderMain, draw: draw, writeHash: writeHash,
    };
    host.replaceChildren(el("div", { class: "cx-databody" }, V.list, V.main));
    ui.onTray = function () { if (V && V.ui === ui) { V.writeHash(); V.renderList(); V.draw(); } };
    new ResizeObserver(function () { if (V && V.host === host && host.clientWidth) V.plots.forEach(function (u) { u.setSize({ width: laneWidth(), height: u.height }); }); }).observe(host);
  }
  function laneWidth() { return Math.max(300, V.main.clientWidth - 2); }

  function render(host, data, route, ui) {
    if (!V || V.host !== host || V.name !== data.profile.name) mountView(host, data, ui);
    var view = V;
    view.layout = route.params.m === "overlay" ? "overlay" : "stack";
    view.pendingRange = route.params.x ? route.params.x.split("-").map(Number) : null;
    loadSessions(view.name).then(function (sessions) {
      if (V !== view) return;
      view.sessions = sessions;
      var id = route.session;
      if (!id && tray.ids.length) {
        var best = sessions.map(function (s) { return [presence(s).count, s.samples, s.id]; }).sort(function (a, b) { return b[0] - a[0] || b[1] - a[1]; })[0];
        if (best && best[0]) id = best[2];
      }
      var same = id === view.id && view.S;
      if (!same) { view.pins = []; view.range = null; }
      view.id = id;
      view.renderList();
      if (!id) { view.S = null; view.renderMain(); return; }
      if (same) { applyPendingRange(view); view.draw(); return; }
      var token = ++view.token;
      view.main.replaceChildren(el("p", { class: "cx-muted" }, "Loading session…"));
      var entry = sessions.filter(function (s) { return s.id === id; })[0];
      if (!entry) { view.main.replaceChildren(el("p", { class: "cx-muted" }, "No session " + id + " in this profile.")); return; }
      loadDoc(view.name, id).then(function (doc) {
        if (V !== view || token !== view.token) return;
        view.S = prepare(entry, doc, view.defs);
        applyPendingRange(view);
        view.renderMain();
        view.writeHash();
      }, function (err) { view.main.replaceChildren(el("p", { class: "cx-muted" }, "Could not load the session: " + err.message)); });
    }, function (err) { host.replaceChildren(el("p", { class: "cx-muted" }, "Could not load sessions: " + err.message)); });
  }
  function applyPendingRange(view) {
    var r = view.pendingRange;
    view.range = r && r.length === 2 && r[1] > r[0] ? r : null;
    view.pendingRange = null;
  }

  /** How many of the tray's signals a session recorded. */
  function presence(s) {
    var n = 0;
    tray.ids.forEach(function (id) {
      var p = id.split(":"), st = s.streams[p[0] + ":" + p[1]];
      if (st && st.sig.indexOf(p[2]) >= 0) n++;
    });
    return { count: n, total: tray.ids.length };
  }

  function writeHash() {
    if (!V || !V.id) return;
    var s = tray.ids.join(",");
    V.ui.writtenS = s;
    var params = { s: s, m: V.layout === "overlay" ? "overlay" : "", x: V.range ? Math.round(V.range[0]) + "-" + Math.round(V.range[1]) : "" };
    history.replaceState(null, "", X.hashFor({ profile: V.name, mode: "plot", session: V.id, params: params }));
  }
  function open(id) {
    X.go({ profile: V.name, mode: "plot", session: id, params: { s: tray.ids.join(","), m: V.layout === "overlay" ? "overlay" : "" } });
  }

  /* ---------------------------------------------------------- session list */

  function renderList() {
    var states = {};
    V.sessions.forEach(function (s) { s.states.forEach(function (st) { states[st] = (states[st] || 0) + 1; }); });
    var active = Object.keys(V.states).filter(function (k) { return V.states[k]; });
    var q = V.filter.toLowerCase();
    var rows = V.sessions.filter(function (s) {
      return (!q || (s.label + " " + s.date + " " + (s.notes || "")).toLowerCase().indexOf(q) >= 0) &&
        active.every(function (st) { return s.states.indexOf(st) >= 0; });
    }).slice().reverse();
    var input = el("input", { type: "search", class: "cx-input", placeholder: "Filter sessions…", value: V.filter, "aria-label": "Filter sessions",
      oninput: function () { V.filter = input.value; V.renderList(); input.focus(); } });
    V.list.replaceChildren(
      input,
      el("div", { class: "cx-statefilter" }, Object.keys(states).sort().map(function (st) {
        return el("button", { type: "button", class: "cx-chipbtn" + (V.states[st] ? " cx-active" : ""), onclick: function () { V.states[st] = !V.states[st]; V.renderList(); } }, st);
      })),
      el("div", { class: "cx-muted" }, rows.length + " of " + V.sessions.length + " sessions"),
      el("div", { class: "cx-sesslist" }, rows.map(function (s) {
        var p = presence(s), dim = p.total && !p.count;
        return el("button", { type: "button", class: "cx-sess" + (s.id === V.id ? " cx-active" : "") + (dim ? " cx-dim" : ""), onclick: function () { open(s.id); } },
          el("div", {}, el("strong", {}, s.date + " " + s.start.slice(0, 5)), " ", el("span", { class: "cx-muted" }, duration(s.dur)),
            p.total ? el("span", { class: "cx-badge " + (p.count === p.total ? "cx-ok" : p.count ? "cx-warn" : "cx-plain") }, p.count + "/" + p.total) : null),
          el("div", { class: "cx-sesslabel" }, s.label || "(no label)"),
          el("div", { class: "cx-muted" }, s.states.join(" · ")));
      })));
  }

  /* ---------------------------------------------------------- session view */

  function renderMain() {
    var S = V.S;
    if (!S) {
      V.main.replaceChildren(el("div", { class: "cx-empty" }, el("h3", {}, "Pick a session"),
        el("p", {}, "Each session is one recording: a drive, a charge, a parked check. Choose one on the left. " +
          "Put signals in the tray first (＋ on the Definitions side) and the list shows which sessions recorded them.")));
      return;
    }
    var e = S.entry;
    var varying = Object.keys(S.signals).filter(function (k) { return S.signals[k].varies; }).length;
    V.head = el("div", { class: "cx-sesshead" },
      el("h3", {}, e.label || e.date),
      el("div", { class: "cx-muted" }, e.date + " " + e.start + " · " + duration(S.dur) + " · " + e.samples + " samples · " + varying + " signals vary · " +
        e.states.join(", ") + (e.transport ? " · " + e.transport : "")),
      e.keep_mode === "changes" ? el("div", { class: "cx-note" }, "Recorded on change only: a flat stretch is a held value, so lines are drawn as steps.") : null,
      e.quality && (e.quality.drop || e.quality.stale) ? el("div", { class: "cx-note" }, "Link quality: " + (e.quality.drop || 0) + " dropped and " + (e.quality.stale || 0) + " stale frames during this recording.") : null,
      e.notes ? el("details", {}, el("summary", {}, "Notes"), X.notes(e.notes)) : null);
    V.heat = el("div", { class: "cx-heat" });
    V.controls = el("div", { class: "cx-controls" });
    V.lanes = el("div", { class: "cx-lanes" });
    V.readout = el("div", { class: "cx-readout" });
    V.discover = el("div", { class: "cx-discover" });
    V.main.replaceChildren(V.head, V.heat, V.controls, V.lanes, V.readout, V.discover, V.cli = el("div", {}));
    if (window.canairDiscover) window.canairDiscover.heat(V);
    V.draw();
  }

  function setRange(a, b) {
    var dur = V.S.dur / 1000;
    V.range = b - a >= dur * 0.98 ? null : [Math.max(0, a), Math.min(dur, b)];
    V.plots.forEach(function (u) { u.setScale("x", V.range ? { min: V.range[0], max: V.range[1] } : { min: 0, max: dur }); });
    afterRange();
  }
  function afterRange() {
    clearTimeout(V.rangeTimer);
    V.rangeTimer = setTimeout(function () {
      writeHash();
      if (window.canairDiscover) window.canairDiscover.rangeChanged(V);
    }, 150);
  }

  function draw() {
    if (!V.S || !V.lanes) return;
    V.plots.forEach(function (u) { u.destroy(); });
    V.plots = []; V.cells = {};
    var S = V.S, keys = tray.ids.filter(function (k) { return S.signals[k]; });
    var missing = tray.ids.filter(function (k) { return !S.signals[k]; });
    V.controls.replaceChildren(
      el("div", { class: "cx-seg" },
        ["stack", "overlay"].map(function (m) {
          return el("button", { type: "button", class: "cx-segbtn" + (V.layout === m ? " cx-active" : ""), title: m === "stack" ? "One lane per unit" : "All signals on one axis, each scaled to its own min and max",
            onclick: function () { V.layout = m; writeHash(); draw(); } }, m === "stack" ? "Stacked" : "Overlay (normalized)");
        })),
      el("button", { type: "button", class: "cx-button", onclick: function () { setRange(0, S.dur / 1000); } }, "reset zoom"),
      el("button", { type: "button", class: "cx-button", onclick: function () { if (navigator.clipboard) navigator.clipboard.writeText(location.href); } }, "copy link"),
      el("span", { class: "cx-muted" }, "Drag to zoom · click to pin a point, click again for a second to see the difference"));
    if (!keys.length) {
      V.lanes.replaceChildren(el("div", { class: "cx-empty" },
        el("p", {}, tray.ids.length ? "None of the tray's signals were recorded in this session." : "Nothing to plot yet."),
        el("p", { class: "cx-muted" }, "Click a row in the overview above to add that signal, or add signals with ＋ under Definitions. " +
          "Sessions in the list show how many of your tray's signals they recorded.")));
      V.readout.replaceChildren(); V.cli.replaceChildren();
      if (window.canairDiscover) window.canairDiscover.panels(V);
      return;
    }
    var al = align(S, keys);
    var groups = V.layout === "overlay" ? [{ unit: "", keys: keys, normalized: true }] : groupByUnit(keys);
    V.lanes.replaceChildren();
    var width = laneWidth();
    groups.forEach(function (g, gi) {
      var holder = el("div", { class: "cx-lane" });
      var ribbon = gi === 0 ? ribbonRows() : [];
      V.lanes.appendChild(holder);
      V.plots.push(makePlot(holder, g, al, width, gi === groups.length - 1, ribbon));
    });
    renderReadout(keys);
    renderCli(keys);
    if (V.range) V.plots.forEach(function (u) { u.setScale("x", { min: V.range[0], max: V.range[1] }); });
    if (window.canairDiscover) window.canairDiscover.panels(V);
  }

  function groupByUnit(keys) {
    var by = {}, order = [];
    keys.forEach(function (k) {
      var u = V.S.signals[k].unit || "(no unit)";
      if (!by[u]) { by[u] = []; order.push(u); }
      by[u].push(k);
    });
    var groups = [];
    order.forEach(function (u) {
      for (var i = 0; i < by[u].length; i += MAX_PER_LANE) groups.push({ unit: u === "(no unit)" ? "" : u, keys: by[u].slice(i, i + MAX_PER_LANE) });
    });
    return groups;
  }

  /** One row per vehicle state: the time ranges the session spent in it. */
  function ribbonRows() {
    var S = V.S, spans = S.spans, names = [];
    spans.forEach(function (sp) { sp.states.forEach(function (st) { if (names.indexOf(st) < 0) names.push(st); }); });
    return names.slice(0, 8).map(function (st) {
      var segs = [];
      spans.forEach(function (sp, i) {
        var on = sp.states.indexOf(st) >= 0, end = i + 1 < spans.length ? spans[i + 1].t : S.dur;
        if (!on) return;
        var last = segs[segs.length - 1];
        if (last && last[1] === sp.t) last[1] = end; else segs.push([sp.t, end]);
      });
      return { state: st, segs: segs, color: "hsl(" + hue(st) + ",55%,50%)" };
    });
  }

  function makePlot(holder, g, al, width, isLast, ribbon) {
    var S = V.S, fg = css("--md-default-fg-color--light"), grid = css("--md-default-fg-color--lightest"), text = css("--md-default-fg-color");
    var data = [al.x], series = [{}];
    g.keys.forEach(function (k) {
      var ys = al.ys[k];
      if (g.normalized) {
        var sig = S.signals[k], lo = Infinity, hi = -Infinity;
        for (var i = 0; i < sig.v.length; i++) if (sig.v[i] === sig.v[i]) { lo = Math.min(lo, sig.v[i]); hi = Math.max(hi, sig.v[i]); }
        ys = ys.map(function (v) { return v === null ? null : (hi > lo ? (v - lo) / (hi - lo) : 0.5); });
      }
      data.push(ys);
      var sg = S.signals[k];
      series.push({ label: sg.name, stroke: colorOf(k), width: 1.6, dash: sg.verified ? undefined : [6, 4], points: { show: false }, spanGaps: false,
        paths: window.uPlot.paths.stepped({ align: 1 }) });
    });
    var ribbonH = ribbon.length ? ribbon.length * RIBBON_ROW + 6 : 0;
    var height = (g.normalized ? OVERLAY_H : LANE_H) + ribbonH + (isLast ? 28 : 0);
    var opts = {
      width: width, height: height, padding: [ribbonH + 6, 12, 0, 0], legend: { show: false },
      scales: { x: { time: false, range: function (u, min, max) { return [min, max]; } }, y: g.normalized ? { range: [-0.03, 1.03] } : {} },
      axes: [
        { size: isLast ? 28 : 0, show: isLast, stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, incrs: TIME_INCRS,
          values: function (u, splits) { return splits.map(function (v) { return clock(S.entry.start, v); }); } },
        { size: AXIS_W, stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid },
          label: g.normalized ? "normalized (own min–max)" : g.unit, labelSize: 14, labelFont: "11px sans-serif",
          values: function (u, splits) { return g.normalized ? splits.map(function (v) { return v === 0 || v === 1 ? String(v) : ""; }) : splits.map(fmt); } },
      ],
      series: series,
      cursor: { sync: { key: "cx", scales: ["x", null] }, drag: { x: true, y: false, setScale: true }, points: { size: 6 } },
      hooks: {
        setScale: [function (u, key) {
          if (key !== "x" || u !== V.plots[0]) return;
          var min = u.scales.x.min, max = u.scales.x.max, dur = S.dur / 1000;
          V.range = max - min >= dur * 0.98 ? null : [min, max];
          afterRange();
        }],
        setCursor: [function (u) {
          if (u !== V.plots[0]) return;
          updateCursorReadout(u.cursor.left >= 0 ? u.posToVal(u.cursor.left, "x") : null);
        }],
        draw: [function (u) { drawOverlays(u, ribbon, ribbonH, text); }],
      },
    };
    var plot = new window.uPlot(opts, data, holder);
    plot.height = height;
    plot.over.addEventListener("mousedown", function (e) { plot._down = e.clientX; });
    plot.over.addEventListener("mouseup", function (e) {
      if (plot._down === undefined || Math.abs(e.clientX - plot._down) > 3) return;
      var t = plot.posToVal(e.clientX - plot.over.getBoundingClientRect().left, "x");
      V.pins = V.pins.length >= 2 ? [] : V.pins.concat([t]);
      V.plots.forEach(function (u) { u.redraw(false); });
      updatePins();
    });
    return plot;
  }

  function drawOverlays(u, ribbon, ribbonH, textColor) {
    var ctx = u.ctx, dpr = devicePixelRatio || 1, b = u.bbox;
    ctx.save();
    ribbon.forEach(function (row, i) {
      var y = b.top - ribbonH * dpr + (i * RIBBON_ROW + 3) * dpr;
      ctx.fillStyle = row.color;
      row.segs.forEach(function (sg) {
        var x0 = Math.max(u.valToPos(sg[0] / 1000, "x", true), b.left), x1 = Math.min(u.valToPos(sg[1] / 1000, "x", true), b.left + b.width);
        if (x1 > x0) ctx.fillRect(x0, y, x1 - x0, (RIBBON_ROW - 3) * dpr);
      });
    });
    if (ribbon.length && u === V.plots[0]) {
      ctx.fillStyle = textColor; ctx.font = (9 * dpr) + "px sans-serif"; ctx.textAlign = "right";
      ribbon.forEach(function (row, i) { ctx.fillText(row.state, b.left - 6 * dpr, b.top - ribbonH * dpr + (i * RIBBON_ROW + RIBBON_ROW - 1) * dpr); });
    }
    ctx.beginPath(); ctx.rect(b.left, b.top, b.width, b.height); ctx.clip();
    ctx.strokeStyle = textColor; ctx.lineWidth = dpr; ctx.setLineDash([4 * dpr, 3 * dpr]);
    V.pins.forEach(function (t) {
      var x = u.valToPos(t, "x", true);
      ctx.beginPath(); ctx.moveTo(x, b.top); ctx.lineTo(x, b.top + b.height); ctx.stroke();
    });
    ctx.restore();
  }

  /* --------------------------------------------------------------- readout */

  function renderReadout(keys) {
    var S = V.S;
    V.cells = {};
    var rows = keys.map(function (k) {
      var sg = S.signals[k], c = V.cells[k] = { cur: el("td", { class: "cx-num" }), a: el("td", { class: "cx-num" }), b: el("td", { class: "cx-num" }), d: el("td", { class: "cx-num" }), r: el("td", { class: "cx-num cx-muted" }) };
      return el("tr", { class: V.focus === k ? "cx-focused" : null },
        el("td", {}, el("i", { class: "cx-swatch", style: "background:" + colorOf(k) })),
        el("td", {}, el("button", { type: "button", class: "cx-link", title: "Find related signals", onclick: function () { V.focus = k; draw(); } }, el("strong", {}, sg.name)),
          " ", el("span", { class: "cx-muted" }, sg.ecu + ":" + sg.pid), sg.verified ? null : [" ", el("span", { class: "cx-badge cx-warn" }, "unverified")]),
        el("td", { class: "cx-muted" }, sg.unit), c.cur, c.a, c.b, c.d, c.r,
        el("td", {}, el("button", { type: "button", class: "cx-x", "aria-label": "Remove " + sg.name, onclick: function () { tray.remove(k); } }, "×")));
    });
    V.readout.replaceChildren(el("table", { class: "cx-table cx-readout-table" },
      el("thead", {}, el("tr", {}, ["", "Signal", "Unit", "At cursor", "Pin A", "Pin B", "B − A", "per min", ""].map(function (t) { return el("th", {}, t); }))),
      el("tbody", {}, rows)));
    V.keys = keys;
    updatePins();
  }
  function updateCursorReadout(tSec) {
    if (!V.keys) return;
    V.keys.forEach(function (k) {
      V.cells[k].cur.textContent = tSec === null ? "" : fmt(valueAt(V.S.signals[k], tSec * 1000));
    });
  }
  function updatePins() {
    if (!V.keys) return;
    var p = V.pins.slice().sort(function (a, b) { return a - b; });
    V.keys.forEach(function (k) {
      var sg = V.S.signals[k], c = V.cells[k];
      var a = p.length > 0 ? valueAt(sg, p[0] * 1000) : NaN, b = p.length > 1 ? valueAt(sg, p[1] * 1000) : NaN;
      c.a.textContent = p.length > 0 ? fmt(a) : "";
      c.b.textContent = p.length > 1 ? fmt(b) : "";
      c.d.textContent = p.length > 1 ? fmt(b - a) : "";
      c.r.textContent = p.length > 1 && p[1] > p[0] ? fmt((b - a) / ((p[1] - p[0]) / 60)) : "";
    });
    if (p.length) {
      V.readout.querySelector("thead th:nth-child(5)").textContent = "A @ " + clock(V.S.entry.start, p[0]);
      V.readout.querySelector("thead th:nth-child(6)").textContent = p.length > 1 ? "B @ " + clock(V.S.entry.start, p[1]) : "Pin B";
    }
  }

  function renderCli(keys) {
    var e = V.S.entry, scope = ' --date ' + e.date + (e.label ? ' --label "' + e.label.replace(/"/g, "") + '"' : "");
    var cmds = [];
    if (keys.length > 1) cmds.push("canair --profile " + V.name + " align " + keys.join(" ") + scope);
    if (V.focus && V.S.signals[V.focus]) cmds.push("canair --profile " + V.name + " correlate uds --against " + V.focus + scope);
    V.cli.replaceChildren(cmds.length ? el("div", {}, el("h4", {}, "Continue in the terminal"), cmds.map(X.command)) : null);
  }

  api.valueAt = valueAt; api.fmt = fmt; api.setRange = setRange; api.colorOf = colorOf; api.duration = duration;
  X.dataView = { render: render };
})();
