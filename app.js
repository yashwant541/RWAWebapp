/* Category Formula Studio - front end (vanilla JS, no build step). */
(function () {
  "use strict";

  // ---------------------------------------------------------------- backend URL
  function apiBase() {
    try {
      if (typeof getWebAppBackendUrl === "function") return getWebAppBackendUrl("");
    } catch (e) { /* not in Dataiku */ }
    return "";
  }
  const API = apiBase().replace(/\/$/, "");

  let ADMIN_TOKEN = null;
  try { ADMIN_TOKEN = sessionStorage.getItem("cfs_admin_token"); } catch (e) {}

  async function call(path, opts) {
    opts = opts || {};
    const headers = opts.headers || {};
    if (ADMIN_TOKEN && path.indexOf("/api/admin") === 0) headers["X-Admin-Token"] = ADMIN_TOKEN;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
    }
    const res = await fetch(API + path, { method: opts.method || "GET", headers, body: opts.body });
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || (data && data.ok === false)) {
      const msg = (data && (data.error || (data.problems || []).join("; "))) || ("HTTP " + res.status);
      const err = new Error(msg); err.data = data; err.status = res.status; throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- helpers
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));
  const el = (tag, attrs, kids) => {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    });
    (kids || []).forEach((c) => n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  };

  function toast(msg, kind) {
    const t = el("div", { class: "toast " + (kind || ""), text: msg });
    $("#toast-wrap").appendChild(t);
    setTimeout(() => t.remove(), 4200);
  }

  // Compact multi-select dropdown. Returns the wrapper element; call ._get() for the
  // ordered list of checked values (order follows `options`).
  function multiSelect(options, selected) {
    const chosen = new Set(selected || []);
    const btn = el("button", { type: "button", class: "msel-btn" });
    const panel = el("div", { class: "msel-panel hidden" });
    options.forEach((o) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = chosen.has(o);
      cb.addEventListener("change", () => { cb.checked ? chosen.add(o) : chosen.delete(o); paint(); });
      panel.appendChild(el("label", { class: "msel-opt" }, [cb, el("span", { text: o })]));
    });
    function paint() {
      const arr = options.filter((o) => chosen.has(o));
      btn.textContent = arr.length ? arr.join(", ") : "Select columns";
      btn.classList.toggle("placeholder", !arr.length);
    }
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const wasOpen = !panel.classList.contains("hidden");
      $$(".msel-panel").forEach((p) => p.classList.add("hidden"));
      panel.classList.toggle("hidden", wasOpen);
    });
    const wrap = el("div", { class: "msel" }, [btn, panel]);
    document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) panel.classList.add("hidden"); });
    paint();
    wrap._get = () => options.filter((o) => chosen.has(o));
    return wrap;
  }

  function confirmModal(title, body) {
    return new Promise((resolve) => {
      $("#modal-title").textContent = title;
      $("#modal-body").textContent = body;
      $("#modal").classList.remove("hidden");
      const done = (v) => { $("#modal").classList.add("hidden"); ok.remove(); cancel.remove(); resolve(v); };
      const ok = $("#modal-ok").cloneNode(true);
      const cancel = $("#modal-cancel").cloneNode(true);
      $("#modal-ok").replaceWith(ok); $("#modal-cancel").replaceWith(cancel);
      ok.addEventListener("click", () => done(true));
      cancel.addEventListener("click", () => done(false));
    });
  }

  function show(view) {
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + view).classList.remove("hidden");
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- router
  // Hash-based pages so each screen is addressable and the browser Back
  // button works:  #/  ->  categories     #/c/<id>  ->  a category
  //                #/admin  ->  admin console
  function go(hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  }

  function route() {
    const parts = (location.hash || "#/").replace(/^#\/?/, "").split("/").filter(Boolean);
    if (parts[0] === "c" && parts[1]) return openCategory(decodeURIComponent(parts[1]));
    if (parts[0] === "admin") return openAdmin(parts[1] ? decodeURIComponent(parts[1]) : null);
    return loadHome();
  }

  // ================================================================ HOME
  async function loadHome() {
    show("home");
    const grid = $("#cat-grid");
    grid.innerHTML = "<p class='muted'>Loading…</p>";
    try {
      const { categories } = await call("/api/categories");
      grid.innerHTML = "";
      categories.forEach((c) => {
        let pill;
        if (c.configured) pill = el("span", { class: "pill ok", text: "Ready" });
        else if (c.n_canonical || c.folders_wired) pill = el("span", { class: "pill warn", text: "Setup incomplete" });
        else pill = el("span", { class: "pill neutral", text: "Not set up" });
        grid.appendChild(el("div", { class: "cat-card", onclick: () => go("#/c/" + encodeURIComponent(c.id)) }, [
          el("div", { class: "cat-badge", text: (c.name || c.id).slice(0, 2).toUpperCase() }),
          el("div", { class: "cat-name", text: c.name || c.id }),
          el("div", { class: "cat-meta", text: `${c.n_canonical} input cols · ${c.n_computed} computed` }),
          el("div", {}, [pill]),
        ]));
      });
      if (!categories.length) grid.innerHTML = "<p class='muted'>No categories defined.</p>";
    } catch (e) { grid.innerHTML = "<p class='err'>" + e.message + "</p>"; }
  }

  // ================================================================ CATEGORY
  let CUR = null; // {id, name, recipe-ish}

  async function openCategory(id) {
    show("category");
    setStep(1);
    const { categories } = await call("/api/categories");
    const c = categories.find((x) => x.id === id) || { id, name: id };
    CUR = c;
    $("#cat-title").textContent = c.name || id;
    $("#cat-sub").textContent = c.configured
      ? `${c.n_canonical} input columns → ${c.n_computed} computed columns`
      : "This category is not fully set up yet — an admin must upload a sample.";
    refreshInputs();
  }

  function setStep(n) {
    $$("#stepper li").forEach((li) => {
      const s = +li.dataset.step;
      li.classList.toggle("active", s === n);
      li.classList.toggle("done", s < n);
    });
    $$(".step-panel").forEach((p) => p.classList.add("hidden"));
    $("#step-" + n).classList.remove("hidden");
  }

  async function refreshInputs() {
    const tb = $("#input-table tbody");
    tb.innerHTML = "";
    try {
      const { files } = await call(`/api/category/${CUR.id}/files?folder=input`);
      if (!files.length) tb.innerHTML = "<tr><td colspan='2' class='muted'>No input files uploaded.</td></tr>";
      files.forEach((f) => tb.appendChild(el("tr", {}, [
        el("td", { text: f.name }),
        el("td", {}, [
          el("button", { class: "icon-btn dl", title: "Download", text: "↓",
            onclick: () => dl("input", f.path) }),
          el("button", { class: "icon-btn", title: "Delete", text: "✕",
            onclick: () => delFile("input", f.path, f.name) }),
        ]),
      ])));
    } catch (e) { tb.innerHTML = "<tr><td colspan='2' class='err'>" + e.message + "</td></tr>"; }
  }

  function dl(folder, path) {
    window.open(`${API}/api/category/${CUR.id}/download?folder=${folder}&path=${encodeURIComponent(path)}`, "_blank");
  }

  async function delFile(folder, path, name) {
    if (!(await confirmModal("Delete file", `Delete "${name}" from ${folder}? This cannot be undone.`))) return;
    await call(`/api/category/${CUR.id}/delete`, { method: "POST", json: { folder, path } });
    toast("Deleted " + name, "ok");
    refreshInputs(); refreshOutputs();
  }

  async function uploadFiles(fileList) {
    if (!fileList.length) return;
    const fd = new FormData();
    Array.prototype.forEach.call(fileList, (f) => fd.append("files", f));
    try {
      const r = await call(`/api/category/${CUR.id}/upload`, { method: "POST", body: fd });
      toast(`Uploaded ${r.saved.length} file(s)`, "ok");
      refreshInputs();
    } catch (e) { toast(e.message, "err"); }
  }

  async function runValidate() {
    setStep(2);
    const tb = $("#validate-table tbody");
    tb.innerHTML = "<tr><td colspan='5' class='muted'>Validating…</td></tr>";
    try {
      const { results } = await call(`/api/category/${CUR.id}/validate`, { method: "POST", json: {} });
      tb.innerHTML = "";
      if (!results.length) tb.innerHTML = "<tr><td colspan='5' class='muted'>Nothing to validate.</td></tr>";
      results.forEach((r) => tb.appendChild(el("tr", {}, [
        el("td", { text: r.file }),
        el("td", { text: r.header_row != null ? r.header_row + 1 : "–" }),
        el("td", { text: r.rows != null ? r.rows : "–" }),
        el("td", {}, [el("span", { class: "pill " + (r.ok ? "ok" : "err"), text: r.ok ? "Valid" : "Invalid" })]),
        el("td", { class: "small muted", text: r.message || "" }),
      ])));
    } catch (e) { tb.innerHTML = "<tr><td colspan='5' class='err'>" + e.message + "</td></tr>"; }
  }

  async function runCompute() {
    setStep(3);
    $("#compute-status").textContent = "Running…";
    const tb = $("#compute-table tbody");
    tb.innerHTML = "";
    try {
      const { results } = await call(`/api/category/${CUR.id}/compute`, { method: "POST", json: {} });
      const ok = results.filter((r) => r.ok).length;
      $("#compute-status").textContent = `${ok} of ${results.length} file(s) computed.`;
      results.forEach((r) => tb.appendChild(el("tr", {}, [
        el("td", { text: r.file }),
        el("td", {}, [el("span", { class: "pill " + (r.ok ? "ok" : "err"), text: r.ok ? "OK" : "Failed" })]),
        el("td", { text: r.output || "–" }),
        el("td", { text: r.rows != null ? r.rows : "–" }),
        el("td", { class: "small muted", text: (r.errors && r.errors.length ? r.errors.join("; ") : (r.message || "")) }),
      ])));
      refreshOutputs();
    } catch (e) { $("#compute-status").innerHTML = "<span class='err'>" + e.message + "</span>"; }
  }

  async function refreshOutputs() {
    for (const which of ["output", "template"]) {
      const tb = $(`#${which}-table tbody`);
      tb.innerHTML = "";
      try {
        const { files } = await call(`/api/category/${CUR.id}/files?folder=${which}`);
        if (!files.length) { tb.innerHTML = "<tr><td colspan='2' class='muted'>Empty.</td></tr>"; continue; }
        files.forEach((f) => tb.appendChild(el("tr", {}, [
          el("td", { text: f.name }),
          el("td", {}, [
            el("button", { class: "icon-btn dl", title: "Download", text: "↓", onclick: () => dl(which, f.path) }),
            el("button", { class: "icon-btn", title: "Delete", text: "✕", onclick: () => delGeneric(which, f.path, f.name) }),
          ]),
        ])));
      } catch (e) { tb.innerHTML = "<tr><td colspan='2' class='err'>" + e.message + "</td></tr>"; }
    }
  }

  async function delGeneric(folder, path, name) {
    if (!(await confirmModal("Delete file", `Delete "${name}"?`))) return;
    await call(`/api/category/${CUR.id}/delete`, { method: "POST", json: { folder, path } });
    toast("Deleted " + name, "ok"); refreshOutputs();
  }

  async function clearFolder(folder) {
    if (!(await confirmModal("Clear folder", `Delete ALL files in the ${folder} folder for ${CUR.name}?`))) return;
    const r = await call(`/api/category/${CUR.id}/delete`, { method: "POST", json: { folder, all: true } });
    toast(`Removed ${r.deleted} file(s)`, "ok");
    folder === "input" ? refreshInputs() : refreshOutputs();
  }

  async function runCompare() {
    setStep(5);
    $("#compare-status").textContent = "Comparing…";
    const tb = $("#compare-summary tbody");
    tb.innerHTML = "";
    $("#compare-download").innerHTML = "";
    try {
      const r = await call(`/api/category/${CUR.id}/compare`, { method: "POST", json: {} });
      $("#compare-status").textContent = "Comparison complete.";
      (r.summary || []).forEach((s) => tb.appendChild(el("tr", {}, [
        el("td", { text: s.source_file }),
        el("td", { text: s.rows }),
        el("td", { text: s.matched_rows }),
        el("td", {}, [el("span", { class: "pill " + (s.mismatched_rows ? "err" : "ok"), text: s.mismatched_rows })]),
        el("td", { text: s.diffs != null ? s.diffs : s.mismatch_findings }),
      ])));
      const dlBox = $("#compare-download");
      (r.issues || []).forEach((i) => dlBox.appendChild(el("div", { class: "warnbox",
        text: `⚠ ${i.source_file}: column “${i.column}” is empty — ${i.note}` })));
      (r.notes || []).forEach((n) => dlBox.appendChild(el("div", { class: "warnbox", text: "• " + n })));
      dlBox.appendChild(el("button", { class: "btn", text: "Download " + r.output,
        onclick: () => dl("output", "/" + r.output) }));
    } catch (e) { $("#compare-status").innerHTML = "<span class='err'>" + e.message + "</span>"; }
  }

  // ================================================================ ADMIN
  let ADMIN_CATS = [];
  let ADMIN_ACTIVE = null;
  let FOLDER_OPTIONS = [];

  async function openAdmin(catId) {
    show("admin");
    if (ADMIN_TOKEN) { await enterAdminPanel(catId); }
    else { $("#admin-login").classList.remove("hidden"); $("#admin-panel").classList.add("hidden"); }
  }

  async function doLogin() {
    $("#login-err").textContent = "";
    try {
      const r = await call("/api/login", { method: "POST", json: {
        username: $("#login-user").value, password: $("#login-pass").value } });
      ADMIN_TOKEN = r.token;
      try { sessionStorage.setItem("cfs_admin_token", r.token); } catch (e) {}
      if (r.must_change) {
        await enterAdminPanel("__settings__");
        toast("Please change the default credentials.", "err");
      } else {
        route();
      }
    } catch (e) { $("#login-err").textContent = e.message; }
  }

  async function enterAdminPanel(wantTab) {
    $("#admin-login").classList.add("hidden");
    $("#admin-panel").classList.remove("hidden");
    const { categories } = await call("/api/categories");
    ADMIN_CATS = categories;
    try { FOLDER_OPTIONS = (await call("/api/admin/folders")).folders; } catch (e) { FOLDER_OPTIONS = []; }
    const tabs = $("#admin-tabs"); tabs.innerHTML = "";
    categories.forEach((c) => tabs.appendChild(el("button", { text: c.name || c.id, "data-tab": c.id,
      onclick: () => selectAdminTab(c.id) })));
    tabs.appendChild(el("button", { text: "⚙ Settings", "data-tab": "__settings__",
      onclick: () => selectAdminTab("__settings__") }));
    const valid = wantTab && (wantTab === "__settings__" || categories.some((c) => c.id === wantTab));
    selectAdminTab(valid ? wantTab : (categories[0] ? categories[0].id : "__settings__"));
  }

  function selectAdminTab(id) {
    ADMIN_ACTIVE = id;
    $$("#admin-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
    try {
      history.replaceState(null, "", "#/admin" + (id === "__settings__" ? "" : "/" + encodeURIComponent(id)));
    } catch (e) {}
    id === "__settings__" ? renderSettingsTab() : renderCategoryTab(id);
  }

  function folderField(key, val) {
    const input = el("input", { type: "text", value: val || "", list: "folder-dl", "data-fkey": key,
      placeholder: "managed folder id" });
    return el("div", { class: "field" }, [el("label", { text: key }), input]);
  }

  async function renderCategoryTab(id) {
    const body = $("#admin-tab-body");
    body.innerHTML = "<p class='muted'>Loading…</p>";
    const { recipe } = await call(`/api/admin/category/${id}`);
    if (ADMIN_ACTIVE !== id) return;  // a newer tab click won the race
    body.innerHTML = "";

    // datalist for folder ids
    let dl = $("#folder-dl");
    if (!dl) { dl = el("datalist", { id: "folder-dl" }); document.body.appendChild(dl); }
    dl.innerHTML = "";
    FOLDER_OPTIONS.forEach((f) => dl.appendChild(el("option", { value: f.id, text: f.name })));

    // --- rename
    const nameInput = el("input", { type: "text", value: recipe.name || id });
    body.appendChild(el("div", { class: "admin-section" }, [
      el("h3", { text: "Name" }),
      nameInput,
      el("div", { class: "row-actions" }, [
        el("button", { class: "btn ghost", text: "Save name", onclick: async () => {
          await call(`/api/admin/category/${id}/rename`, { method: "POST", json: { name: nameInput.value } });
          toast("Renamed", "ok"); enterAdminPanel(ADMIN_ACTIVE);
        } }),
      ]),
    ]));

    // --- folders
    const fWrap = el("div", { class: "grid-2" },
      ["input", "mapping", "output", "template"].map((k) => folderField(k, (recipe.folders || {})[k])));
    body.appendChild(el("div", { class: "admin-section" }, [
      el("h3", { text: "Managed folders" }),
      fWrap,
      el("div", { class: "row-actions" }, [
        el("button", { class: "btn ghost", text: "Save folders", onclick: async () => {
          const folders = {};
          $$("[data-fkey]", fWrap).forEach((i) => folders[i.dataset.fkey] = i.value.trim());
          try {
            await call(`/api/admin/category/${id}/folders`, { method: "POST", json: { folders } });
            toast("Folders saved", "ok");
          } catch (e) { toast(e.message, "err"); }
        } }),
      ]),
    ]));

    // --- sample upload
    const sampleInput = el("input", { type: "file", accept: ".xlsx,.xls" });
    const analysisBox = el("div", {});
    body.appendChild(el("div", { class: "admin-section" }, [
      el("h3", { text: "Sample workbook" }),
      el("p", { class: "muted small", text: "Upload the sample .xlsx (data sheet + mapping/lookup sheets). The app detects the header row, the formula columns, lookups and toggle references." }),
      sampleInput,
      el("div", { class: "row-actions" }, [
        el("button", { class: "btn", text: "Analyse sample", onclick: async () => {
          if (!sampleInput.files[0]) return toast("Choose a file first", "err");
          const fd = new FormData(); fd.append("file", sampleInput.files[0]);
          try {
            const r = await call(`/api/admin/category/${id}/sample`, { method: "POST", body: fd });
            renderAnalysis(id, recipe, r.analysis, analysisBox);
            toast("Sample parsed", "ok");
          } catch (e) { toast(e.message, "err"); }
        } }),
      ]),
      analysisBox,
    ]));

    // if a recipe already exists, render its editor straight away
    if ((recipe.computed_columns || []).length || (recipe.canonical_schema || []).length) {
      renderAnalysis(id, recipe, recipeToAnalysis(recipe), analysisBox);
    }
  }

  function recipeToAnalysis(recipe) {
    return {
      data_sheet: recipe.data_sheet, header_row: recipe.header_row,
      canonical_schema: recipe.canonical_schema || [],
      computed_columns: recipe.computed_columns || [],
      lookups: recipe.lookups || Object.keys(recipe.lookup_tables || {}).map((s) => ({ sheet: s })),
      lookup_tables: recipe.lookup_tables || {},
      toggles: recipe.toggles || [],
      comparison: recipe.comparison || [],
      comparison_keys: recipe.comparison_keys || [],
      warnings: [],
    };
  }

  function renderAnalysis(id, recipe, a, box) {
    box.innerHTML = "";
    const allCols = () => a.canonical_schema.concat(a.computed_columns.map((c) => c.name));

    (a.warnings || []).forEach((w) => box.appendChild(el("div", { class: "warnbox", text: "⚠ " + w })));

    box.appendChild(el("div", { class: "field" }, [
      el("label", { text: "Data sheet" }),
      el("input", { type: "text", value: a.data_sheet || "", id: "an-datasheet" }),
    ]));

    box.appendChild(el("h4", { text: "Input (non-computed) columns — exact-match schema" }));
    box.appendChild(el("div", { class: "chip-list" },
      a.canonical_schema.map((c) => el("span", { class: "chip", text: c }))));

    box.appendChild(el("h4", { text: "Computed columns" }));
    const compWrap = el("div", {});
    a.computed_columns.forEach((c, i) => {
      const expr = el("textarea", { text: c.pandas_expr || "" });
      compWrap.appendChild(el("div", { class: "admin-section" }, [
        el("div", { class: "chip computed", text: c.name }),
        el("div", { class: "field" }, [el("label", { text: "Excel formula (written live into every output row)" }),
          el("input", { type: "text", value: c.excel_formula || "", "data-cc-xl": i })]),
        el("div", { class: "field" }, [el("label", { text: "Python / pandas expression (used for the comparison values)" }), expr]),
        (c.notes && c.notes.length) ? el("div", { class: "warnbox", text: "translator notes: " + c.notes.join("; ") }) : el("span", {}),
      ]));
      expr.dataset.ccExpr = i;
    });
    box.appendChild(compWrap);

    box.appendChild(el("h4", { text: "Lookup tables (embedded in every output as extra sheets)" }));
    box.appendChild(el("div", { class: "chip-list" },
      Object.keys(a.lookup_tables || {}).map((s) => el("span", { class: "chip",
        text: `${s} (${(a.lookup_tables[s].records || []).length} rows)` }))));

    // toggles
    box.appendChild(el("h4", { text: "Toggles (constant Yes/No values used in formulas)" }));
    const togWrap = el("div", {});
    function addToggleRow(t) {
      const nm = el("input", { type: "text", value: (t && t.name) || "", placeholder: "toggle name" });
      const vl = el("select", {}, [el("option", { value: "Yes", text: "Yes" }), el("option", { value: "No", text: "No" })]);
      if (t && t.value) vl.value = t.value;
      const row = el("div", { class: "grid-2", "data-toggle": "1" }, [nm, el("div", {}, [vl,
        el("button", { class: "icon-btn", text: "✕", onclick: () => row.remove() })])]);
      row._get = () => ({ name: nm.value.trim(), value: vl.value });
      togWrap.appendChild(row);
    }
    (a.toggles || []).forEach(addToggleRow);
    box.appendChild(togWrap);
    box.appendChild(el("button", { class: "btn ghost small", text: "+ toggle", onclick: () => addToggleRow() }));

    // comparison rules
    box.appendChild(el("h4", { text: "Comparison rules (columns vs columns, same row)" }));
    box.appendChild(el("p", { class: "muted small", text: "Pick one or more columns on each side. Columns are compared position-by-position (1st ↔ 1st, 2nd ↔ 2nd …); pick a single column on one side to compare it against several on the other. A row is 'matched' only if every pair agrees." }));
    const cmpWrap = el("div", {});
    const asArr = (v) => Array.isArray(v) ? v.slice() : (v ? [v] : []);
    function addCmpRow(r) {
      const left = multiSelect(allCols(), asArr(r && r.left));
      const right = multiSelect(allCols(), asArr(r && r.right));
      const type = el("select", {}, [el("option", { value: "numeric", text: "numeric" }), el("option", { value: "text", text: "text" })]);
      const tol = el("input", { type: "text", value: (r && r.tolerance != null) ? r.tolerance : "0", placeholder: "tolerance" });
      if (r) { type.value = r.type || "numeric"; }
      const row = el("div", { class: "cmp-row", "data-cmp": "1" }, [
        el("div", {}, [el("label", { text: "left column(s)" }), left]),
        el("div", {}, [el("label", { text: "right column(s)" }), right]),
        el("div", {}, [el("label", { text: "type" }), type]),
        el("div", {}, [el("label", { text: "num. tolerance" }), tol]),
        el("div", { class: "cmp-rm" }, [
          el("button", { class: "icon-btn", title: "Remove rule", text: "✕", onclick: () => row.remove() })]),
      ]);
      row._get = () => ({ left: left._get(), right: right._get(), type: type.value, tolerance: parseFloat(tol.value) || 0 });
      cmpWrap.appendChild(row);
    }
    (a.comparison || []).forEach(addCmpRow);
    box.appendChild(cmpWrap);
    box.appendChild(el("button", { class: "btn ghost small", text: "+ rule", onclick: () => addCmpRow() }));

    // save
    box.appendChild(el("div", { class: "row-actions" }, [
      el("button", { class: "btn", text: "Save recipe", onclick: () => saveRecipe(id, recipe, a, box, false) }),
    ]));
  }

  async function saveRecipe(id, recipe, a, box, force) {
    const computed = a.computed_columns.map((c, i) => Object.assign({}, c, {
      excel_formula: ($(`[data-cc-xl="${i}"]`, box) || {}).value || c.excel_formula,
      pandas_expr: ($(`[data-cc-expr="${i}"]`, box) || {}).value || c.pandas_expr,
    }));
    const toggles = $$("[data-toggle]", box).map((r) => r._get()).filter((t) => t.name);
    const comparison = $$("[data-cmp]", box).map((r) => r._get())
      .filter((r) => r.left.length && r.right.length);
    const payload = {
      recipe: {
        data_sheet: ($("#an-datasheet", box) || {}).value || a.data_sheet,
        canonical_schema: a.canonical_schema,
        computed_columns: computed,
        lookups: a.lookups || [],
        lookup_tables: a.lookup_tables || {},
        toggles, comparison,
      },
      force: !!force,
    };
    try {
      const r = await call(`/api/admin/category/${id}/recipe`, { method: "POST", json: payload });
      if (r.needs_confirmation) {
        const go = await confirmModal("Unresolved references",
          r.problems.join("\n") + "\n\nSave anyway?");
        if (go) return saveRecipe(id, recipe, a, box, true);
        return;
      }
      toast("Recipe saved" + (r.problems && r.problems.length ? " (with warnings)" : ""), "ok");
    } catch (e) { toast(e.message, "err"); }
  }

  function renderSettingsTab() {
    const body = $("#admin-tab-body");
    body.innerHTML = "";
    const u = el("input", { type: "text", placeholder: "new username (optional)" });
    const cur = el("input", { type: "password", placeholder: "current password" });
    const np = el("input", { type: "password", placeholder: "new password (min 6)" });
    body.appendChild(el("div", { class: "admin-section" }, [
      el("h3", { text: "Change admin credentials" }),
      el("label", { text: "New username" }), u,
      el("label", { text: "Current password" }), cur,
      el("label", { text: "New password" }), np,
      el("div", { class: "row-actions" }, [
        el("button", { class: "btn", text: "Update", onclick: async () => {
          try {
            await call("/api/admin/change-credentials", { method: "POST", json: {
              new_username: u.value || undefined, current_password: cur.value, new_password: np.value } });
            toast("Credentials updated — sign in again", "ok");
            ADMIN_TOKEN = null; try { sessionStorage.removeItem("cfs_admin_token"); } catch (e) {}
            openAdmin();
          } catch (e) { toast(e.message, "err"); }
        } }),
      ]),
    ]));
    body.appendChild(el("div", { class: "admin-section" }, [
      el("h3", { text: "Session" }),
      el("button", { class: "btn ghost", text: "Sign out", onclick: () => {
        ADMIN_TOKEN = null; try { sessionStorage.removeItem("cfs_admin_token"); } catch (e) {}
        openAdmin();
      } }),
    ]));
  }

  // ================================================================ wire up
  function init() {
    $("#nav-home").addEventListener("click", () => go("#/"));
    $("#brand-home").addEventListener("click", () => go("#/"));
    $("#nav-admin").addEventListener("click", () => go("#/admin"));
    $("#cat-back").addEventListener("click", () => go("#/"));
    $("#admin-back").addEventListener("click", () => go("#/"));
    $("#login-btn").addEventListener("click", doLogin);
    $("#login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });

    $("#browse-btn").addEventListener("click", () => $("#file-input").click());
    $("#file-input").addEventListener("change", (e) => uploadFiles(e.target.files));
    const dz = $("#dropzone");
    ["dragover", "dragenter"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

    $("#clear-inputs").addEventListener("click", () => clearFolder("input"));
    $("#to-validate").addEventListener("click", runValidate);
    $("#to-compute").addEventListener("click", runCompute);
    $("#to-compare").addEventListener("click", runCompare);
    $$("[data-goto]").forEach((b) => b.addEventListener("click", () => setStep(+b.dataset.goto)));
    $$("[data-clear]").forEach((b) => b.addEventListener("click", () => clearFolder(b.dataset.clear)));
    $$('#stepper li').forEach((li) => li.addEventListener("click", () => setStep(+li.dataset.step)));

    window.addEventListener("hashchange", route);
    // some embedders apply the initial #hash after scripts run; re-route on load too
    window.addEventListener("load", route);
    route();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
