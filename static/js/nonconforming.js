// SMS NonConforming — frontend logic. Talks to /nonconforming/api/*.
// Loaded after app.js (both `defer`, so DOM order = execution order).
(function () {
  const PAGE_SIZE = 50;

  const state = {
    q: "",
    status: "",
    offset: 0,
    total: 0,
    editingId: null,
  };

  const FIELD_LABELS = {
    ticket_no: "Ticket #",
    model: "Model #",
    serial: "Serial #",
    ra_no: "RA #",
    tracking: "Tracking",
    carrier: "Carrier",
    address: "Address",
    status: "Status",
    ussi_resolution: "USSI Resolution Confirmation",
    addtl_info: "Addtl Info",
    origin_company: "Origin Company",
    store_no: "Store #",
    rack: "Rack",
    bin: "Bin",
  };
  const REQUIRED = ["model", "serial", "carrier"];

  function $(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ── Load / render table ───────────────────────────────────────────────
  async function loadItems() {
    const params = new URLSearchParams({
      limit: PAGE_SIZE, offset: state.offset,
    });
    if (state.q) params.set("q", state.q);
    if (state.status) params.set("status", state.status);
    let data;
    try {
      const res = await fetch(`/nonconforming/api/items?${params}`);
      data = await res.json();
    } catch (e) {
      return;
    }
    if (!data.ok) return;
    state.total = data.total;
    renderTable(data.items);
    renderStatusOptions(data.statuses);
    const preview = $("nc-next-number");
    if (preview) preview.textContent = data.next_number_preview || "—";
    updatePager();
  }

  function renderTable(items) {
    const body = $("nc-table-body");
    const empty = $("nc-empty");
    if (!body) return;
    if (!items.length) {
      body.innerHTML = "";
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");
    body.innerHTML = items.map(it => `
      <tr class="border-b border-steel/10 hover:bg-ink-100/50 transition-colors">
        <td class="py-2 pr-3 font-mono text-accent">${escapeHtml(it.number)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.date_added)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.model)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.serial)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.rack)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.bin)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.ticket_no)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.carrier)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.status)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.filed_by_username)}</td>
        <td class="py-2 pr-3 whitespace-nowrap">
          <button class="text-steel hover:text-accent transition-colors mr-2" data-action="label" data-id="${it.id}">Label</button>
          <button class="text-steel hover:text-accent transition-colors mr-2" data-action="edit" data-id="${it.id}">Edit</button>
          <button class="text-steel/50 hover:text-accent transition-colors" data-action="download-label" data-id="${it.id}" title="Download .zpl (fallback if Browser Print isn't reachable)">⭳</button>
        </td>
      </tr>
    `).join("");
  }

  function renderStatusOptions(statuses) {
    const sel = $("nc-status-filter");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">All statuses</option>' +
      statuses.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    sel.value = current;
    const list = $("nc-status-list");
    if (list) list.innerHTML = statuses.map(s => `<option value="${escapeHtml(s)}"></option>`).join("");
  }

  function updatePager() {
    const count = $("nc-count");
    if (count) {
      const from = state.total === 0 ? 0 : state.offset + 1;
      const to = Math.min(state.offset + PAGE_SIZE, state.total);
      count.textContent = `${from}–${to} of ${state.total}`;
    }
    const prev = $("nc-prev"), next = $("nc-next");
    if (prev) prev.disabled = state.offset === 0;
    if (next) next.disabled = state.offset + PAGE_SIZE >= state.total;
  }

  // ── Add item ─────────────────────────────────────────────────────────
  function bindAddForm() {
    const form = $("nc-add-form");
    if (!form) return;
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const errBox = $("nc-add-error");
      const btn = $("nc-add-btn");
      const statusEl = $("nc-add-status");
      errBox.classList.add("hidden");
      const fd = new FormData(form);
      const payload = {};
      for (const [k, v] of fd.entries()) payload[k] = v;
      btn.disabled = true;
      statusEl.textContent = "Filing…";
      try {
        const res = await fetch("/nonconforming/api/items", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!data.ok) {
          errBox.textContent = data.error || "Could not file item.";
          errBox.classList.remove("hidden");
          statusEl.textContent = "";
        } else {
          form.reset();
          statusEl.textContent = `Filed as ${data.item.number}`;
          setTimeout(() => { statusEl.textContent = ""; }, 4000);
          state.offset = 0;
          loadItems();
        }
      } catch (e) {
        errBox.textContent = "Network error — item was not saved.";
        errBox.classList.remove("hidden");
        statusEl.textContent = "";
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ── Search / filter / pagination ────────────────────────────────────
  function bindSearch() {
    let t;
    const search = $("nc-search");
    if (search) search.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => { state.q = search.value; state.offset = 0; loadItems(); }, 250);
    });
    const statusFilter = $("nc-status-filter");
    if (statusFilter) statusFilter.addEventListener("change", () => {
      state.status = statusFilter.value; state.offset = 0; loadItems();
    });
    const prev = $("nc-prev");
    if (prev) prev.addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - PAGE_SIZE); loadItems();
    });
    const next = $("nc-next");
    if (next) next.addEventListener("click", () => {
      if (state.offset + PAGE_SIZE < state.total) { state.offset += PAGE_SIZE; loadItems(); }
    });
  }

  // ── Export ───────────────────────────────────────────────────────────
  function bindExport() {
    const btn = $("nc-export-btn");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const params = new URLSearchParams();
      if (state.q) params.set("q", state.q);
      if (state.status) params.set("status", state.status);
      window.location.href = `/nonconforming/api/export?${params}`;
    });
  }

  // ── Row action delegation (edit / label buttons) ────────────────────
  function bindTableActions() {
    const body = $("nc-table-body");
    if (!body) return;
    body.addEventListener("click", e => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      if (btn.dataset.action === "edit") openEditModal(id);
      if (btn.dataset.action === "label") printLabel(id, btn);
      if (btn.dataset.action === "download-label") downloadLabel(id);
    });
  }

  // ── Toast (small, corner, auto-dismiss — not a blocking modal) ─────
  function toast(message, isError) {
    let el = $("nc-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "nc-toast";
      el.className = "fixed bottom-6 right-6 z-[9999] font-mono text-xs rounded-lg px-4 py-3 shadow-lg transition-opacity";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.style.background = isError ? "#ef4444" : "#1a2029";
    el.style.color = isError ? "#fff" : "#2fd8a6";
    el.style.border = isError ? "1px solid #f87171" : "1px solid #2fd8a6";
    el.style.opacity = "1";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.style.opacity = "0"; }, isError ? 4000 : 2500);
  }

  // ── Zebra Browser Print — talk to the local agent's HTTP API directly
  // (http://localhost:9100) rather than requiring Zebra's proprietary
  // BrowserPrint-*.min.js file to be manually installed. Same endpoints
  // that file itself calls under the hood: GET /default, POST /write.
  const BP_BASE = "http://localhost:9100/";

  function withTimeout(promise, ms) {
    return Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
    ]);
  }

  function parseDefaultPrinter(text) {
    // Browser Print's /default returns a tab/newline-delimited block like:
    //   "Name:\n\tZDesigner GK420d\n\tDevice Type:\n\tPrinter\n\t..."
    // (7 "\n\t"-joined fields — see the reference wrapper this mirrors).
    const parts = text.split("\n\t");
    if (parts.length !== 7) throw new Error("unexpected /default response");
    const clean = s => s.split(":").slice(1).join(":").trim();
    return {
      name: clean(parts[1]),
      deviceType: clean(parts[2]),
      connection: clean(parts[3]),
      uid: clean(parts[4]),
      provider: clean(parts[5]),
      manufacturer: clean(parts[6]),
      version: 0,
    };
  }

  async function getDefaultZebraPrinter() {
    const res = await withTimeout(fetch(BP_BASE + "default", {
      method: "GET",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
    }), 3000);
    const text = await res.text();
    return parseDefaultPrinter(text);
  }

  async function sendZpl(device, zpl) {
    const res = await withTimeout(fetch(BP_BASE + "write", {
      method: "POST",
      headers: { "Content-Type": "text/plain;charset=UTF-8" },
      body: JSON.stringify({ device, data: zpl }),
    }), 5000);
    if (!res.ok) throw new Error("write failed");
  }

  async function printLabel(id, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/nonconforming/api/items/${id}/label`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "could not build label");
      const device = await getDefaultZebraPrinter();
      await sendZpl(device, data.zpl);
      toast(`Printed ${data.number} → ${device.name}`, false);
    } catch (e) {
      toast("Label Error, Check Printer", true);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function downloadLabel(id) {
    window.location.href = `/nonconforming/api/items/${id}/label?download=1`;
  }

  // ── Edit modal ───────────────────────────────────────────────────────
  async function openEditModal(id) {
    const res = await fetch(`/nonconforming/api/items/${id}`);
    const data = await res.json();
    if (!data.ok) return;
    state.editingId = id;
    const item = data.item;
    $("nc-edit-number").textContent = item.number;
    const form = $("nc-edit-form");
    form.innerHTML = Object.keys(FIELD_LABELS).map(key => `
      <div class="${key === "address" || key === "addtl_info" ? "md:col-span-2" : ""}">
        <label class="block text-xs text-steel mb-1.5 uppercase tracking-wide font-mono">
          ${FIELD_LABELS[key]}${REQUIRED.includes(key) ? ' <span class="text-warn">*</span>' : ""}
        </label>
        <input type="text" name="${key}" value="${escapeHtml(item[key])}"
          class="w-full bg-ink-100 border border-steel/50 rounded-lg px-3 py-2.5 text-sm text-slate-200 transition-all" />
      </div>
    `).join("");
    $("nc-edit-error").classList.add("hidden");
    $("nc-edit-modal").classList.remove("hidden");
  }

  function closeEditModal() {
    $("nc-edit-modal").classList.add("hidden");
    state.editingId = null;
  }

  function bindEditModal() {
    const closeBtn = $("nc-edit-close");
    if (closeBtn) closeBtn.addEventListener("click", closeEditModal);
    const modal = $("nc-edit-modal");
    if (modal) modal.addEventListener("click", e => { if (e.target === modal) closeEditModal(); });

    const saveBtn = $("nc-edit-save");
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const form = $("nc-edit-form");
      const fd = new FormData(form);
      const payload = {};
      for (const [k, v] of fd.entries()) payload[k] = v;
      const errBox = $("nc-edit-error");
      try {
        const res = await fetch(`/nonconforming/api/items/${state.editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!data.ok) {
          errBox.textContent = data.error || "Could not save.";
          errBox.classList.remove("hidden");
          return;
        }
        closeEditModal();
        loadItems();
      } catch (e) {
        errBox.textContent = "Network error — changes were not saved.";
        errBox.classList.remove("hidden");
      }
    });

    const deleteBtn = $("nc-edit-delete");
    if (deleteBtn) deleteBtn.addEventListener("click", async () => {
      if (!state.editingId) return;
      if (!confirm("Delete this record? This can't be undone.")) return;
      await fetch(`/nonconforming/api/items/${state.editingId}`, { method: "DELETE" });
      closeEditModal();
      loadItems();
    });
  }

  function refreshFilerBadge() {
    const badge = $("nc-filer-badge");
    if (badge && window.NC_USER && window.NC_USER.username) {
      badge.textContent = window.NC_USER.username;
    }
  }

  function init() {
    if (!$("nc-add-form")) return; // section not present for this user
    refreshFilerBadge();
    bindAddForm();
    bindSearch();
    bindExport();
    bindTableActions();
    bindEditModal();
    loadItems();
  }

  // Reload whenever the SMS NonConforming tab becomes active (in case
  // another tab/user changed data in the meantime), without refetching on
  // every unrelated portal-tab switch.
  function wrapShowPortalPage() {
    const prev = window.showPortalPage;
    if (typeof prev !== "function") return;
    window.showPortalPage = function (portal) {
      prev(portal);
      if (portal === "sms-nonconforming") loadItems();
    };
  }

  window.addEventListener("DOMContentLoaded", () => {
    init();
    wrapShowPortalPage();
  });
})();
