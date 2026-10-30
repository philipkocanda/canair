/* Profile explorer: browse a bundled profile's ECUs, PIDs, signals and IO controls.
 *
 * Renders the JSON that scripts/mkdocs_hooks/explorer.py writes at docs build time
 * (assets/explorer/data/<profile>.json). All content decisions live in
 * canlib/explorer_export.py; this file only draws. No framework, no bundler.
 * State lives in the URL hash: #<profile>/<ECU>/<tab or PID>/<signal>.
 * Plan: plans/2026-10-21-profile-explorer.md
 */
(function () {
  "use strict";

  var SCRIPT_URL = document.currentScript && document.currentScript.src;
  if (!SCRIPT_URL) return;
  var DATA_URL = new URL("data/", SCRIPT_URL);
  var SAFETY_URL = new URL("../../concepts/safety/", SCRIPT_URL).href;
  var TABS = [
    ["overview", "Overview"],
    ["pids", "PIDs"],
    ["io", "IO control"],
    ["research", "Research"],
  ];
  var BROADCAST = "@broadcast";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var WIDE_KEY = "canair-explorer-wide";
  function storedWide() { try { return localStorage.getItem(WIDE_KEY) === "1"; } catch (e) { return false; } }
  function storeWide(on) { try { localStorage.setItem(WIDE_KEY, on ? "1" : "0"); } catch (e) { /* not remembered */ } }

  var cache = {};
  var app = null; // the mounted explorer, replaced on every (instant) page load

  function el(tag, attrs) {
    return build(document.createElement(tag), attrs, Array.prototype.slice.call(arguments, 2));
  }
  function svg(tag, attrs) {
    return build(document.createElementNS(SVG_NS, tag), attrs, Array.prototype.slice.call(arguments, 2));
  }
  /** Run a re-render without letting the page shrink while it is half-built.
   *  Safari has no scroll anchoring, and WebKit can lay the page out mid-swap (it does when the
   *  tapped, :hover/:active button is among the removed nodes, and whenever we measure). A page
   *  measured half-replaced is shorter, so the scroll position is clamped and the page jumps;
   *  holding its height until the new content has been laid out prevents that. */
  var holding = false;
  function keepingHeight(fn) {
    if (holding) return fn();
    var body = document.body;
    holding = true;
    body.style.minHeight = document.documentElement.scrollHeight + "px";
    try { return fn(); } finally {
      void body.offsetHeight;
      body.style.minHeight = "";
      holding = false;
    }
  }
  /** replaceChildren that skips null/false and flattens arrays, like el(). */
  function fill(node) {
    var next = build(document.createDocumentFragment(), {}, Array.prototype.slice.call(arguments, 1));
    if (!node.isConnected || !node.firstChild) { node.replaceChildren(next); return node; }
    return keepingHeight(function () { node.replaceChildren(next); return node; });
  }
  function build(node, attrs, kids) {
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    kids.forEach(function add(kid) {
      if (Array.isArray(kid)) return kid.forEach(add);
      if (kid === null || kid === undefined || kid === false) return;
      node.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
    });
    return node;
  }

  function loadProfile(name) {
    if (!cache[name]) {
      cache[name] = fetch(new URL(name + ".json", DATA_URL)).then(function (r) {
        if (!r.ok) throw new Error(name + ": HTTP " + r.status);
        return r.json();
      });
    }
    return cache[name];
  }

  /* ---------------------------------------------------------------- routing */

  // #<profile>/<ECU>/<tab or PID>/<signal>   browsing definitions
  // #<profile>/plot/<session>?s=ECU:PID:NAME,...&x=<from>-<to>&m=stack|overlay   plotting captures
  function parseHash() {
    var raw = location.hash.replace(/^#/, "");
    var q = raw.indexOf("?");
    // Split before decoding so an encoded "/" stays inside its part; a malformed escape is kept raw.
    var parts = (q >= 0 ? raw.slice(0, q) : raw).split("/").map(function (p) {
      try { return decodeURIComponent(p); } catch (e) { return p; }
    });
    var r = { profile: parts[0] || "", mode: "defs", ecu: parts[1] || "", tab: "overview", pid: "", signal: "", session: "", params: {} };
    if (parts[1] === "plot") {
      r.mode = "plot"; r.ecu = ""; r.session = parts[2] || "";
      try {
        new URLSearchParams(q >= 0 ? raw.slice(q + 1) : "").forEach(function (v, k) { r.params[k] = v; });
      } catch (e) { /* unreadable query: plot with defaults */ }
      return r;
    }
    if (parts[2]) {
      if (TABS.some(function (t) { return t[0] === parts[2]; })) r.tab = parts[2];
      else { r.tab = "pids"; r.pid = parts[2]; r.signal = parts[3] || ""; }
    }
    return r;
  }
  function hashFor(r) {
    var parts = [r.profile];
    var query = "";
    if (r.mode === "plot") {
      parts.push("plot");
      if (r.session) parts.push(r.session);
      query = Object.keys(r.params || {}).filter(function (k) { return r.params[k]; }).map(function (k) {
        return k + "=" + encodeURIComponent(r.params[k]).replace(/%3A/g, ":").replace(/%2C/g, ",");
      }).join("&");
    } else if (r.ecu) {
      parts.push(r.ecu);
      if (r.pid) parts.push(r.pid, r.signal || "");
      else if (r.tab && r.tab !== "overview") parts.push(r.tab);
    }
    return "#" + parts.map(encodeURIComponent).join("/").replace(/\/+$/, "") + (query ? "?" + query : "");
  }
  // pushState, not location.hash: Material's instant navigation treats the first hash change
  // after a page load as a navigation, refetches the page and remounts the explorer, losing its
  // in-memory state. Back/forward still fire hashchange, which re-renders.
  function go(r) {
    var target = hashFor(r);
    if (target === location.hash) return;
    history.pushState(null, "", target);
    if (app && app.show) app.show();
  }

  /* --------------------------------------------------------------- the tray */

  // Signals picked for plotting, as "ECU:PID:NAME". Lives across mode switches and ECU changes.
  var tray = {
    profile: "", ids: [], listeners: [],
    has: function (id) { return this.ids.indexOf(id) >= 0; },
    set: function (ids, profile) {
      var next = ids.filter(function (id, i) { return id && ids.indexOf(id) === i; });
      if (profile === undefined) profile = this.profile;
      if (profile === this.profile && next.join() === this.ids.join()) return;
      this.profile = profile; this.ids = next; this.emit();
    },
    add: function (id) { if (!this.has(id)) this.set(this.ids.concat([id])); },
    remove: function (id) { this.set(this.ids.filter(function (x) { return x !== id; })); },
    toggle: function (id) { if (this.has(id)) this.remove(id); else this.add(id); },
    onchange: function (fn) { this.listeners.push(fn); },
    emit: function () { this.listeners.slice().forEach(function (fn) { fn(); }); },
  };
  function trayId(ecu, pid, name) { return ecu + ":" + pid + ":" + name; }
  var PLOTTABLE = { numeric: 1, enum: 1, bitmask: 1, bcd: 1 };
  function plottable(sig) { return !sig.type || PLOTTABLE[sig.type]; }
  function plusButton(ecu, pid, name) {
    var id = trayId(ecu, pid, name);
    return el("button", { type: "button", class: "cx-plus", "data-id": id, title: "Add to the plot tray",
      "aria-label": "Plot " + name, "aria-pressed": tray.has(id) ? "true" : "false",
      onclick: function () { tray.toggle(id); } }, tray.has(id) ? "✓" : "＋");
  }
  function refreshPlusButtons() {
    document.querySelectorAll(".cx-plus").forEach(function (b) {
      var on = tray.has(b.getAttribute("data-id"));
      b.textContent = on ? "✓" : "＋";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.classList.toggle("cx-on", on);
    });
  }

  /* ---------------------------------------------------------------- helpers */

  function srcLink(data, src, label) {
    if (!src) return null;
    return el("a", {
      class: "cx-src", target: "_blank", rel: "noopener", title: src.file + ":" + src.line,
      href: data.profile.blob_base + src.file + "#L" + src.line,
    }, label || "yaml ↗");
  }
  function badge(text, kind) { return el("span", { class: "cx-badge cx-" + kind }, text); }
  function verifiedBadge(ok) { return ok ? badge("verified", "ok") : badge("unverified", "warn"); }
  function notes(text) { return text ? el("div", { class: "cx-notes" }, text) : null; }
  // Long notes are cut to a few lines where the table becomes cards (narrow screens), with a toggle.
  var CLAMP_CHARS = 160;
  function longNotes(text) {
    if (!text || text.length <= CLAMP_CHARS) return notes(text);
    var box = el("div", { class: "cx-notes cx-clamp" }, text);
    var more = el("button", { type: "button", class: "cx-more", "aria-expanded": "false", onclick: function () {
      var open = box.classList.toggle("cx-open");
      more.textContent = open ? "show less" : "show more";
      more.setAttribute("aria-expanded", open ? "true" : "false");
    } }, "show more");
    return [box, more];
  }
  /** Scroll a freshly rendered panel into view when the user picked it but it starts off-screen. */
  function reveal(node) {
    var top = node.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * 0.6) node.scrollIntoView({ block: "start" });
  }
  function command(cmd) {
    var btn = el("button", { type: "button", class: "cx-copy", title: "Copy command", onclick: function () {
      if (navigator.clipboard) navigator.clipboard.writeText(cmd);
      btn.textContent = "copied";
      setTimeout(function () { btn.textContent = "copy"; }, 1200);
    } }, "copy");
    return el("div", { class: "cx-cmd" }, el("code", {}, cmd), btn);
  }
  function facts(rows) {
    return el("table", { class: "cx-facts" }, rows.filter(function (r) { return r[1] !== null && r[1] !== undefined && r[1] !== ""; })
      .map(function (r) { return el("tr", {}, el("th", {}, r[0]), el("td", {}, r[1])); }));
  }
  function ecuByName(data, name) {
    return data.ecus.filter(function (e) { return e.name === name; })[0];
  }

  /* ---------------------------------------------------------------- diagram */

  var D = { labelW: 150, colW: 86, laneH: 56, nodeW: 76, nodeH: 24, top: 14 };

  function renderDiagram(data, selected) {
    var layout = data.layout;
    if (!layout.nodes.length) return el("p", { class: "cx-muted" }, "No bus information in this profile.");
    var width = D.labelW + layout.columns * D.colW + 12;
    var height = D.top * 2 + layout.lanes.length * D.laneH;
    var laneY = function (i) { return D.top + i * D.laneH + D.laneH / 2; };
    var colX = function (c) { return D.labelW + c * D.colW + D.colW / 2; };
    var root = svg("svg", { class: "cx-diagram", width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img",
      "aria-label": "CAN bus diagram", style: "--cx-w:" + width + "px" });

    layout.lanes.forEach(function (lane, i) {
      var rate = lane.bitrate ? " · " + lane.bitrate / 1000 + " kbit/s" : "";
      root.appendChild(svg("line", { class: "cx-lane" + (lane.code === "unplaced" ? " cx-lane-unplaced" : ""), x1: 6, x2: width - 6, y1: laneY(i), y2: laneY(i) }));
      root.appendChild(svg("text", { class: "cx-lane-label", x: 8, y: laneY(i) - 10 }, lane.name));
      root.appendChild(svg("text", { class: "cx-lane-rate", x: 8, y: laneY(i) + 14 }, lane.code + rate));
    });

    layout.nodes.forEach(function (node) {
      var ecu = ecuByName(data, node.name);
      var x = colX(node.col);
      var first = laneY(node.lanes[0]);
      var last = laneY(node.lanes[node.lanes.length - 1]);
      var maturity = ecu ? ecu.maturity : "none";
      var title = ecu
        ? node.name + (ecu.tx_id ? " · tx " + ecu.tx_id : "") + (ecu.rx_id ? " · rx " + ecu.rx_id : "") + " · " + ecu.buses.join(", ")
        : node.name;
      var pick = function () { if (app) app.reveal = true; go({ profile: data.profile.name, ecu: node.name, tab: "overview" }); };
      var g = svg("g", {
        class: "cx-node cx-k-" + node.kind + " cx-m-" + maturity + (node.name === selected ? " cx-selected" : ""),
        tabindex: ecu ? 0 : null, role: ecu ? "button" : null,
        onclick: ecu ? pick : null,
        onkeydown: ecu ? function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } : null,
      }, svg("title", {}, title));
      if (last > first) g.appendChild(svg("line", { class: "cx-bridge", x1: x, x2: x, y1: first + D.nodeH / 2, y2: last }));
      node.lanes.slice(1).forEach(function (l) { g.appendChild(svg("circle", { class: "cx-tick", cx: x, cy: laneY(l), r: 4 })); });
      g.appendChild(svg("rect", { class: "cx-box-bg", x: x - D.nodeW / 2, y: first - D.nodeH / 2, width: D.nodeW, height: D.nodeH, rx: 4 }));
      g.appendChild(svg("rect", { class: "cx-box", x: x - D.nodeW / 2, y: first - D.nodeH / 2, width: D.nodeW, height: D.nodeH, rx: 4 }));
      g.appendChild(svg("text", { class: "cx-box-label", x: x, y: first + 4, "text-anchor": "middle" },
        node.name.length > 11 ? node.name.slice(0, 10) + "…" : node.name));
      root.appendChild(g);
    });

    var legend = el("p", { class: "cx-legend" },
      swatch("none", "no signals"), swatch("signals", "signals"), swatch("verified", "has verified signals"),
      el("span", { class: "cx-muted" }, " Approximation: the profile records which segment(s) an ECU is on, not the physical wiring. " +
        "An ECU on several segments is a bridge."));
    return el("div", {}, el("div", { class: "cx-diagram-wrap" }, root), legend);
  }
  function swatch(kind, label) {
    return el("span", { class: "cx-swatch-item" }, el("i", { class: "cx-swatch cx-m-" + kind }), label);
  }

  /* ------------------------------------------------------------------ views */

  function viewOverview(data, ecu) {
    var wake = ecu.wake;
    return el("div", {},
      facts([
        ["Description", ecu.description],
        ["Identity", ecu.confidence ? badge(ecu.confidence, ecu.confidence === "confirmed" || ecu.confidence === "probable" ? "ok" : "warn") : null],
        ["Request id (tx)", ecu.tx_id ? el("code", {}, ecu.tx_id) : null],
        ["Response id (rx)", ecu.rx_id ? el("code", {}, ecu.rx_id) : null],
        ["Identity protocol", ecu.protocol],
        ["CAN buses", ecu.buses.join(", ")],
        ["Vehicle states", ecu.vehicle_states.join(", ")],
        ["Contents", ecu.counts.pids + " PIDs · " + ecu.counts.signals + " signals (" + ecu.counts.verified + " verified) · " +
          ecu.iocontrol.length + " IO controls · " + ecu.research.length + " research items"],
        ["Source", srcLink(data, ecu.src, ecu.src.file + " ↗")],
      ]),
      wake ? el("div", {}, el("h4", {}, "Wake ritual"),
        el("p", {}, "This ECU sleeps within seconds, so canair wakes it first: " + wake.method + (wake.prime_pid ? " using " + wake.prime_pid : "") +
          (wake.attempts ? ", " + wake.attempts + " attempts" : "") + (wake.interval_ms ? " every " + wake.interval_ms + " ms" : "") + "."),
        notes(wake.notes)) : null,
      ecu.dtcs.length ? el("div", {}, el("h4", {}, "Documented DTCs (" + ecu.dtcs.length + ")"),
        el("ul", { class: "cx-list" }, ecu.dtcs.map(function (d) {
          return el("li", {}, el("code", {}, d.code), d.description ? " — " + d.description : "", " ", srcLink(data, d.src));
        }))) : null);
  }

  function present(v) { return v !== undefined && v !== null && v !== ""; }
  function safeUrl(u) { return /^https?:\/\//i.test(u) ? u : null; }
  /** A SIGNAL_NAME that may wrap after its underscores rather than mid-word. */
  function breakable(name) {
    var parts = name.split("_");
    return parts.map(function (part, i) {
      return [i ? el("wbr", {}) : null, part + (i < parts.length - 1 ? "_" : "")];
    });
  }
  function signalRow(data, ecu, pid, sig, route) {
    var range = sig.min !== undefined || sig.max !== undefined ? (present(sig.min) ? sig.min : "…") + " … " + (present(sig.max) ? sig.max : "…") : "";
    return el("tr", { "data-signal": sig.name, class: route.signal === sig.name ? "cx-flash" : null },
      el("td", { class: "cx-c-plus" }, plottable(sig) ? plusButton(ecu.name, pid.id, sig.name) : null),
      el("td", { class: "cx-c-name" }, el("strong", {}, breakable(sig.name)), sig.type ? el("div", { class: "cx-muted" }, "type: " + sig.type) : null),
      el("td", { class: "cx-c-expr" }, sig.expression ? el("code", {}, sig.expression) : null),
      el("td", { class: "cx-c-unit" }, sig.unit || ""),
      el("td", { class: "cx-c-range cx-muted" }, range),
      el("td", { class: "cx-c-ok" }, verifiedBadge(sig.verified)),
      el("td", { class: "cx-c-notes" }, longNotes(sig.notes), sig.source ? el("div", { class: "cx-muted" }, "source: " + sig.source) : null,
        (sig.source_links || []).map(function (u) {
          return el("div", { class: "cx-link-line" }, safeUrl(u) ? el("a", { href: u, target: "_blank", rel: "noopener" }, u) : u);
        })),
      el("td", { class: "cx-c-src" }, srcLink(data, sig.src, "↗")));
  }

  function viewPids(data, ecu, route, verifiedOnly) {
    if (!ecu.pids.length) return el("p", { class: "cx-muted" }, "No PIDs defined for this ECU.");
    return el("div", {}, ecu.pids.map(function (pid) {
      var sigs = pid.signals.filter(function (s) { return !verifiedOnly || s.verified; });
      if (verifiedOnly && !sigs.length) return null;
      var details = el("details", {
        class: "cx-pid", open: route.pid === pid.id || null, "data-pid": pid.id,
        ontoggle: function (e) {
          // Rendering an open <details> fires "toggle" too; don't clobber a signal deep link.
          if (!e.target.open || parseHash().pid === pid.id) return;
          history.replaceState(null, "", hashFor({ profile: data.profile.name, ecu: ecu.name, pid: pid.id }));
        },
      },
        el("summary", {}, el("code", {}, pid.id), " ", badge(pid.status, pid.status === "active" ? "ok" : "warn"), " ",
          el("span", { class: "cx-muted" }, sigs.length + " signal" + (sigs.length === 1 ? "" : "s") +
            (pid.period ? " · every " + pid.period + " ms" : "") + (pid.response_frames ? " · " + pid.response_frames + " frames" : ""))),
        el("div", { class: "cx-pid-body" },
          command("canair --profile " + data.profile.name + " read " + ecu.name + ":" + pid.id),
          notes(pid.notes), srcLink(data, pid.src, "PID in yaml ↗"),
          plotAllButton(ecu, pid, sigs),
          sigs.length ? el("table", { class: "cx-table cx-sigtable" },
            el("thead", {}, el("tr", {}, ["", "Signal", "Expression", "Unit", "Range", "", "Notes", ""].map(function (t) { return el("th", {}, t); }))),
            el("tbody", {}, sigs.map(function (s) { return signalRow(data, ecu, pid, s, route); }))) : el("p", { class: "cx-muted" }, "No signals decoded yet.")));
      return details;
    }));
  }

  var PLOT_ALL_MAX = 12;
  function plotAllButton(ecu, pid, sigs) {
    var ids = sigs.filter(plottable).slice(0, PLOT_ALL_MAX).map(function (s) { return trayId(ecu.name, pid.id, s.name); });
    if (!ids.length) return null;
    var more = sigs.filter(plottable).length > ids.length ? " (first " + ids.length + ")" : "";
    return el("div", {}, el("button", { type: "button", class: "cx-button", onclick: function () {
      tray.set(tray.ids.concat(ids));
    } }, "＋ plot these " + ids.length + " signals" + more));
  }

  function viewIo(data, ecu, verifiedOnly) {
    var rows = ecu.iocontrol.filter(function (c) { return !verifiedOnly || c.verified; });
    return el("div", {},
      el("div", { class: "admonition warning" },
        el("p", { class: "admonition-title" }, "These drive real hardware"),
        el("p", {}, "IO controls actuate relays, motors and lamps on a real car. canair releases them when its session ends, " +
          "but read the ", el("a", { href: SAFETY_URL }, "Safety page"), " first. Unverified frames are guesses.")),
      rows.length ? el("table", { class: "cx-table cx-iotable" },
        el("thead", {}, el("tr", {}, ["DID", "Label", "On", "Off", "Status", "Notes", ""].map(function (t) { return el("th", {}, t); }))),
        el("tbody", {}, rows.map(function (c) {
          return el("tr", {},
            el("td", { class: "cx-c-did" }, el("code", {}, c.did)),
            el("td", { class: "cx-c-label" }, c.label || "", c.hold === false ? el("div", { class: "cx-muted" }, "no hold") : null),
            el("td", { class: "cx-c-on" }, c.on ? el("code", {}, c.on) : el("span", { class: "cx-muted" }, "—")),
            el("td", { class: "cx-c-off" }, c.off ? el("code", {}, c.off) : el("span", { class: "cx-muted" }, "—")),
            el("td", { class: "cx-c-ok" }, verifiedBadge(c.verified)),
            el("td", { class: "cx-c-notes" }, longNotes(c.notes), command("canair --profile " + data.profile.name + " io " + ecu.name + " --did " + c.did)),
            el("td", { class: "cx-c-src" }, srcLink(data, c.src, "↗")));
        }))) : el("p", { class: "cx-muted" }, verifiedOnly ? "No verified IO controls." : "No IO controls defined for this ECU."));
  }

  function viewResearch(data, ecu) {
    if (!ecu.research.length) return el("p", { class: "cx-muted" }, "No research items for this ECU.");
    return el("div", {}, ecu.research.map(function (r) {
      return el("div", { class: "cx-research" },
        el("div", {}, badge(r.status || "open", r.status === "done" ? "ok" : "warn"), " ", r.priority ? [badge(r.priority, "plain"), " "] : null,
          el("strong", {}, r.type || ""), " ", el("code", {}, r.target || ""), " ", srcLink(data, r.src)),
        notes(r.notes), r.what_to_test ? notes("To test: " + r.what_to_test) : null, r.result ? notes("Result: " + r.result) : null);
    }));
  }

  function viewBroadcast(data) {
    return el("div", {},
      el("p", { class: "cx-muted" }, "Frames the ECUs send on their own (not diagnostic replies), from signals/<bus>.yaml."),
      data.broadcast.map(function (m) {
        return el("details", { class: "cx-pid" },
          el("summary", {}, el("code", {}, m.id), " ", el("strong", {}, m.name || ""), " ",
            el("span", { class: "cx-muted" }, (m.tx_ecu ? "from " + m.tx_ecu + " · " : "") + m.bus + " · " + m.signals.length + " signals"), " ", srcLink(data, m.src)),
          el("div", { class: "cx-pid-body" }, el("table", { class: "cx-table cx-bctable" },
            el("thead", {}, el("tr", {}, ["Signal", "Start bit", "Length", "Order", "Scale", "Offset", "Unit", "", "Notes"].map(function (t) { return el("th", {}, t); }))),
            el("tbody", {}, m.signals.map(function (s) {
              return el("tr", {}, el("td", { class: "cx-c-name" }, el("strong", {}, breakable(s.name))), el("td", { class: "cx-c-start" }, s.start_bit),
                el("td", { class: "cx-c-len" }, s.length), el("td", { class: "cx-c-order" }, s.byte_order),
                el("td", { class: "cx-c-scale" }, s.scale), el("td", { class: "cx-c-offset" }, s.offset), el("td", { class: "cx-c-unit" }, s.unit),
                el("td", { class: "cx-c-ok" }, verifiedBadge(s.verified)),
                el("td", { class: "cx-c-notes" }, longNotes(s.notes), " ", srcLink(data, s.src, "↗")));
            })))));
      }));
  }

  /* ------------------------------------------------------------------ search */

  function search(data, query) {
    var q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    var hits = [];
    data.ecus.forEach(function (ecu) {
      ecu.pids.forEach(function (pid) {
        pid.signals.forEach(function (s) {
          if (s.name.toLowerCase().indexOf(q) >= 0) hits.push({ ecu: ecu.name, pid: pid.id, name: s.name, unit: s.unit });
        });
      });
    });
    return hits.slice(0, 60);
  }

  /* ------------------------------------------------------------------- mount */

  function mount() {
    var root = document.getElementById("explorer");
    if (!root || root.getAttribute("data-mounted")) return;
    root.setAttribute("data-mounted", "1");
    var ui = {
      root: root, index: [], data: null, verifiedOnly: false, filter: "",
      select: el("select", { class: "cx-select", "aria-label": "Profile", onchange: function () { go({ profile: ui.select.value }); } }),
      search: el("input", { type: "search", class: "cx-input", placeholder: "Search signal names…", "aria-label": "Search signals", oninput: renderSearch }),
      verified: el("input", { type: "checkbox", onchange: function () { ui.verifiedOnly = ui.verified.checked; renderRail(); renderDetail(); } }),
      results: el("div", { class: "cx-results", hidden: true }),
      stats: el("span", { class: "cx-muted" }),
      wide: el("button", { type: "button", class: "cx-button cx-widebtn", "aria-pressed": "false" }),
      diagram: el("div", {}),
      filterInput: el("input", { type: "search", class: "cx-input", placeholder: "Filter ECUs…", "aria-label": "Filter ECUs",
        oninput: function () { ui.filter = ui.filterInput.value.toLowerCase(); renderRail(); } }),
      list: el("div", { class: "cx-ecu-list" }),
      detail: el("div", { class: "cx-detail" }),
      mode: el("div", { class: "cx-modes" }),
      tray: el("div", { class: "cx-tray", hidden: true }),
      dataHost: el("div", { class: "cx-data", hidden: true }),
    };
    ui.defsBody = el("div", { class: "cx-body" }, el("div", { class: "cx-rail" }, ui.filterInput, ui.list), ui.detail);
    app = ui;

    // Hide Material's left navigation (and let the page use the full window width) for wide plots.
    function setWide(on) {
      document.body.classList.toggle("cx-wide", on);
      ui.wide.setAttribute("aria-pressed", on ? "true" : "false");
      ui.wide.textContent = on ? "⇥ Show menu" : "⇤ Hide menu";
      ui.wide.title = on ? "Bring back the site navigation" : "Hide the site navigation for more room";
    }
    ui.wide.addEventListener("click", function () {
      var on = !document.body.classList.contains("cx-wide");
      storeWide(on); setWide(on);
    });
    setWide(storedWide());

    function renderSearch() {
      var hits = ui.data ? search(ui.data, ui.search.value) : [];
      ui.results.hidden = !ui.search.value.trim();
      fill(ui.results, hits.length
        ? hits.map(function (h) {
          return el("button", { type: "button", class: "cx-hit", onclick: function () {
            ui.search.value = ""; renderSearch();
            go({ profile: ui.data.profile.name, ecu: h.ecu, pid: h.pid, signal: h.name });
          } }, el("strong", {}, h.name), el("span", { class: "cx-muted" }, " " + h.ecu + " › " + h.pid + (h.unit ? " · " + h.unit : "")));
        })
        : [el("span", { class: "cx-muted" }, ui.search.value.trim().length < 2 ? "Keep typing…" : "No matching signal.")]);
    }

    function renderRail() {
      var data = ui.data, route = parseHash();
      if (!data) return;
      var items = data.ecus.filter(function (e) {
        return (!ui.filter || e.name.toLowerCase().indexOf(ui.filter) >= 0 || (e.description || "").toLowerCase().indexOf(ui.filter) >= 0) &&
          (!ui.verifiedOnly || e.counts.verified > 0);
      }).map(function (e) {
        return el("button", { type: "button", class: "cx-ecu" + (e.name === route.ecu ? " cx-active" : ""), onclick: function () {
          ui.reveal = true;
          go({ profile: data.profile.name, ecu: e.name, tab: "overview" });
        } }, el("i", { class: "cx-swatch cx-m-" + e.maturity }), el("strong", {}, e.name), el("span", { class: "cx-muted" }, " " + e.counts.signals));
      });
      if (data.broadcast.length) {
        items.push(el("button", { type: "button", class: "cx-ecu" + (route.ecu === BROADCAST ? " cx-active" : ""), onclick: function () {
          ui.reveal = true;
          go({ profile: data.profile.name, ecu: BROADCAST });
        } }, el("strong", {}, "Broadcast frames"), el("span", { class: "cx-muted" }, " " + data.broadcast.length)));
      }
      fill(ui.list, items.length ? items : [el("span", { class: "cx-muted" }, "No ECUs match.")]);
    }

    function renderDetail() {
      var data = ui.data, route = parseHash();
      if (!data) return;
      var body;
      if (route.ecu === BROADCAST) {
        body = el("div", {}, el("h3", {}, "Broadcast frames"), viewBroadcast(data));
      } else if (!route.ecu) {
        body = el("p", { class: "cx-muted" }, "Select an ECU in the diagram or the list.");
      } else {
        var ecu = ecuByName(data, route.ecu);
        if (!ecu) {
          body = el("p", { class: "cx-muted" }, "No ECU named " + route.ecu + " in this profile.");
        } else {
          var views = {
            overview: function () { return viewOverview(data, ecu); },
            pids: function () { return viewPids(data, ecu, route, ui.verifiedOnly); },
            io: function () { return viewIo(data, ecu, ui.verifiedOnly); },
            research: function () { return viewResearch(data, ecu); },
          };
          body = el("div", {},
            el("h3", {}, ecu.name, " ", el("span", { class: "cx-muted" }, ecu.description || "")),
            el("div", { class: "cx-tabs" }, TABS.map(function (t) {
              var count = { pids: ecu.pids.length, io: ecu.iocontrol.length, research: ecu.research.length }[t[0]];
              return el("button", { type: "button", class: "cx-tab" + (t[0] === route.tab ? " cx-active" : ""), role: "tab", "aria-selected": t[0] === route.tab ? "true" : "false", onclick: function () {
                go({ profile: data.profile.name, ecu: ecu.name, tab: t[0] });
              } }, t[1], count ? el("span", { class: "cx-muted" }, " " + count) : null);
            })),
            views[route.tab]());
        }
      }
      fill(ui.detail, body);
      var flash = ui.detail.querySelector(".cx-flash");
      if (flash) flash.scrollIntoView({ block: "center" });
      else if (ui.reveal) reveal(ui.detail);
      ui.reveal = false;
    }

    function renderModes(route) {
      var name = ui.data ? ui.data.profile.name : route.profile;
      var hasData = ui.data && ui.data.profile.sessions > 0;
      var plot = route.mode === "plot";
      fill(ui.mode, 
        el("button", { type: "button", class: "cx-mode" + (plot ? "" : " cx-active"), onclick: function () { go({ profile: name }); } }, "Definitions"),
        hasData ? el("button", { type: "button", class: "cx-mode" + (plot ? " cx-active" : ""), onclick: function () {
          go({ profile: name, mode: "plot", session: ui.lastSession || "", params: tray.ids.length ? { s: tray.ids.join(",") } : {} });
        } }, "Data", el("span", { class: "cx-muted" }, " " + ui.data.profile.sessions + " sessions")) : null);
    }

    function renderTray() {
      var route = parseHash();
      ui.tray.hidden = !tray.ids.length;
      fill(ui.tray, [el("span", { class: "cx-muted" }, "Plot tray:")].concat(tray.ids.map(function (id) {
        var p = id.split(":");
        return el("span", { class: "cx-chip", title: id }, p[2], el("span", { class: "cx-muted" }, " " + p[0] + ":" + p[1]),
          el("button", { type: "button", class: "cx-x", "aria-label": "Remove " + id, onclick: function () { tray.remove(id); } }, "×"));
      }), [
        el("button", { type: "button", class: "cx-button", onclick: function () { tray.set([]); } }, "clear"),
        route.mode !== "plot" && ui.data && ui.data.profile.sessions ? el("button", { type: "button", class: "cx-button cx-primary", onclick: function () {
          go({ profile: ui.data.profile.name, mode: "plot", session: ui.lastSession || "", params: { s: tray.ids.join(",") } });
        } }, "plot these ▸") : null,
      ]).filter(Boolean));
      refreshPlusButtons();
    }
    tray.listeners = []; // one mounted explorer at a time; drop the previous page's handler
    tray.onchange(function () { if (app === ui) { renderTray(); if (ui.onTray) ui.onTray(); } });

    function show() {
      if (app !== ui || !document.body.contains(root)) return;
      var route = parseHash();
      var name = route.profile || (ui.index[0] && ui.index[0].name);
      if (!name) return;
      if (!route.profile) { location.replace(hashFor({ profile: name })); return; }
      loadProfile(name).then(function (data) {
        // A slower load for a profile the user already left must not overwrite the current one.
        if (app !== ui || parseHash().profile !== name) return;
        var changed = ui.data !== data;
        ui.data = data;
        ui.select.value = name;
        if (tray.profile !== name) tray.set([], name);
        // In plot mode the URL owns the tray (no s= means an empty tray), unless we wrote it ourselves.
        var wanted = route.params.s || "";
        if (route.mode === "plot" && wanted !== ui.writtenS) tray.set(wanted.split(",").filter(Boolean));
        if (route.mode === "plot") ui.writtenS = wanted;
        if (changed) {
          var s = data.profile.stats;
          var variants = data.variants || [];
          ui.stats.textContent = data.profile.car_model + " · " + data.profile.maturity + " · " + s.ecus + " ECUs, " + s.pids + " PIDs, " +
            s.params + " signals (" + s.verified + " verified)" +
            (variants.length ? " · variants: " + variants.map(function (v) { return v.id; }).join(", ") : "");
          ui.stats.title = variants.map(function (v) {
            return v.id + ": " + v.name + (v.production ? " (built " + v.production.join(" – ") + ")" : "") +
              (v.status ? " · " + v.status : "");
          }).join("\n");
          renderSearch();
        }
        var plot = route.mode === "plot" && window.canairExplorer.dataView;
        keepingHeight(function () {
          renderModes(route);
          renderTray();
          ui.diagram.hidden = ui.defsBody.hidden = !!plot;
          ui.dataHost.hidden = !plot;
          if (plot) {
            if (route.session) ui.lastSession = route.session;
            window.canairExplorer.dataView.render(ui.dataHost, data, route, ui);
            return;
          }
          fill(ui.diagram, renderDiagram(data, route.ecu));
          renderRail();
          renderDetail();
        });
      }, function (err) {
        fill(ui.detail, el("p", { class: "cx-muted" }, "Could not load profile data: " + err.message));
      });
    }

    ui.show = show;
    fill(root, 
      el("div", { class: "cx-toolbar" }, ui.select, ui.search, el("label", { class: "cx-check" }, ui.verified, " verified only"), ui.stats, ui.wide),
      ui.results, ui.mode, ui.tray, ui.diagram, ui.defsBody, ui.dataHost);

    fetch(new URL("index.json", DATA_URL)).then(function (r) { return r.json(); }).then(function (index) {
      ui.index = index;
      ui.select.replaceChildren.apply(ui.select, index.map(function (p) { return el("option", { value: p.name }, p.name); }));
      show();
    }, function (err) {
      fill(root, el("p", {}, "Could not load the explorer data: " + err.message));
    });
  }

  // What the data view (plot.js) builds on.
  window.canairExplorer = {
    el: el, svg: svg, fill: fill, keepingHeight: keepingHeight, badge: badge, verifiedBadge: verifiedBadge, notes: notes, command: command,
    reveal: reveal,
    srcLink: srcLink, parseHash: parseHash, hashFor: hashFor, go: go, tray: tray, trayId: trayId,
    loadProfile: loadProfile, ecuByName: ecuByName, DATA_URL: DATA_URL, SCRIPT_URL: SCRIPT_URL,
    current: function () { return app; }, dataView: null,
  };

  window.addEventListener("hashchange", function () { if (app && app.show) app.show(); });
  // Material's instant navigation swaps pages without a reload, so mount on every page load.
  if (window.document$ && window.document$.subscribe) window.document$.subscribe(mount);
  else document.addEventListener("DOMContentLoaded", mount);
})();
