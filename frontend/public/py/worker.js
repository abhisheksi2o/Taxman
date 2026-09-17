/* ASTRA Tax – in-browser backend. Boots Pyodide, installs the vendored pure-Python wheels, unpacks the
   Python domain code and serves the same API contract the FastAPI server exposes. State is persisted in
   IndexedDB; nothing leaves the browser. */
let backend = null;
let pyodide = null;
let base = "";

const post = (m) => self.postMessage(m);
const progress = (stage, message) => post({ type: "progress", stage, message });

function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("astra-tax", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("kv");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function loadState() {
  try {
    const db = await idb();
    return await new Promise((resolve) => {
      const tx = db.transaction("kv", "readonly").objectStore("kv").get("state");
      tx.onsuccess = () => resolve(tx.result || null);
      tx.onerror = () => resolve(null);
    });
  } catch { return null; }
}
async function saveState(json) {
  try {
    const db = await idb();
    await new Promise((resolve) => {
      const tx = db.transaction("kv", "readwrite").objectStore("kv").put(json, "state");
      tx.onsuccess = () => resolve(); tx.onerror = () => resolve();
    });
  } catch { /* storage unavailable – keep running in memory */ }
}
async function init(baseUrl) {
  base = baseUrl.replace(/\/$/, "");
  const manifest = await (await fetch(`${base}/py/manifest.json`, { cache: "no-store" })).json();
  progress("pyodide", "Loading the Python runtime (WebAssembly)…");
  importScripts(`https://cdn.jsdelivr.net/pyodide/v${manifest.pyodide}/full/pyodide.js`);
  pyodide = await loadPyodide({ indexURL: `https://cdn.jsdelivr.net/pyodide/v${manifest.pyodide}/full/` });
  progress("packages", "Loading pydantic…");
  await pyodide.loadPackage(["pydantic", "micropip"]);
  const micropip = pyodide.pyimport("micropip");
  for (const w of manifest.wheels) {
    progress("packages", `Installing ${w.split("-")[0]}…`);
    await micropip.install(`${base}/py/wheels/${w}`);
  }
  progress("app", "Unpacking the tax engine…");
  const zip = await (await fetch(`${base}/py/${manifest.app}?v=${manifest.build}`, { cache: "no-store" })).arrayBuffer();
  pyodide.FS.mkdirTree("/home/pyodide/astra");
  pyodide.unpackArchive(zip, "zip", { extractDir: "/home/pyodide/astra" });
  pyodide.runPython("import sys; sys.path.insert(0, '/home/pyodide/astra')");
  progress("state", "Restoring your workspace…");
  const state = await loadState();
  const mod = pyodide.pyimport("app.browser.backend");
  backend = mod.BrowserBackend(state || "");
  post({ type: "ready" });
}

self.onmessage = async (ev) => {
  const m = ev.data || {};
  if (m.type === "init") {
    try { await init(m.base); } catch (e) { console.error(e); post({ type: "error", message: String(e && e.message ? e.message : e) }); }
    return;
  }
  if (m.type === "request") {
    if (!backend) { post({ type: "response", id: m.id, status: 503, body: { detail: "Engine not ready" } }); return; }
    try {
      const body = JSON.stringify(m.body === undefined ? null : m.body);
      const raw = m.file
        ? backend.handle(m.method, m.path, body, m.file.name, m.file.type, new Uint8Array(m.file.bytes))
        : backend.handle(m.method, m.path, body);
      const res = JSON.parse(raw);
      if (m.method !== "GET" && backend.dirty) {
        // persist before answering so a tab closed right after an action never loses it
        try { await saveState(backend.export_state()); } catch (e) { console.warn("state save failed", e); }
      }
      post({ type: "response", id: m.id, status: res.status, body: res.body });
    } catch (e) {
      console.error(e);
      post({ type: "response", id: m.id, status: 500, body: { detail: "Engine error: " + String(e && e.message ? e.message : e).slice(0, 400) } });
    }
  }
};
