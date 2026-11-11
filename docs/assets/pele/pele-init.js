/* Render the diagrams that superfences leaves in <div class="pele-diagram"> with Pele.
   The Pele bundle loads only on pages that have a diagram. */
(function () {
  "use strict";

  var bundleUrl = document.currentScript.src.replace(/pele-init\.js.*$/, "vendor/pele.global.js");
  var loading = null;
  // Pele switches a wide flowchart from left to right to top to bottom below this width. The
  // diagrams of the docs are wider than the page, so they stay vertical with readable labels.
  var DIRECTION_BREAKPOINT = 1200;
  var KEEP_DIRECTION = /^\s*%%\s*pele:\s*horizontal\b/;

  function loadPele() {
    if (window.pele) return Promise.resolve();
    if (!loading) {
      loading = new Promise(function (resolve, reject) {
        var script = document.createElement("script");
        script.src = bundleUrl;
        script.onload = resolve;
        script.onerror = function () {
          loading = null;
          reject(new Error("Could not load " + bundleUrl));
        };
        document.head.appendChild(script);
      });
    }
    return loading;
  }

  function showSource(el, source) {
    var pre = document.createElement("pre");
    pre.textContent = source;
    el.textContent = "";
    el.appendChild(pre);
  }

  function mountAll() {
    var diagrams = document.querySelectorAll("div.pele-diagram:not([data-pele-mounted])");
    if (!diagrams.length) return;
    loadPele().then(
      function () {
        diagrams.forEach(function (el) {
          if (el.hasAttribute("data-pele-mounted")) return;
          el.setAttribute("data-pele-mounted", "");
          var source = el.textContent;
          el.textContent = "";
          try {
            window.pele.mount(el, source, {
              directionBreakpoint: DIRECTION_BREAKPOINT,
              // A diagram that starts with the comment "%% pele: horizontal" keeps its direction.
              autoDirection: !KEEP_DIRECTION.test(source),
              onError: function (error) {
                console.error("Pele could not render a diagram:", error);
              },
            });
          } catch (error) {
            console.error("Pele could not render a diagram:", error);
            showSource(el, source);
          }
        });
      },
      function (error) {
        console.error(error);
      }
    );
  }

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(mountAll);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountAll);
  } else {
    mountAll();
  }
})();
