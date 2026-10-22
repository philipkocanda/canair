/* Runs the pure analysis kernels off the main thread (see analysis.js). */
importScripts("analysis.js");
onmessage = function (e) {
  var m = e.data;
  try {
    postMessage({ id: m.id, result: self.CanairAnalysis[m.fn].apply(null, m.args) });
  } catch (err) {
    postMessage({ id: m.id, error: String(err) });
  }
};
