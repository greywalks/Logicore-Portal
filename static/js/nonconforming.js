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
    labelItem: null,
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
        <td class="py-2 pr-3">${escapeHtml(it.ticket_no)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.carrier)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.status)}</td>
        <td class="py-2 pr-3">${escapeHtml(it.filed_by_username)}</td>
        <td class="py-2 pr-3 whitespace-nowrap">
          <button class="text-steel hover:text-accent transition-colors mr-2" data-action="label" data-id="${it.id}">Label</button>
          <button class="text-steel hover:text-accent transition-colors" data-action="edit" data-id="${it.id}">Edit</button>
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
      if (btn.dataset.action === "label") openLabelModal(id);
    });
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

  // ── Label print (Zebra Browser Print) ───────────────────────────────
  async function openLabelModal(id) {
    const res = await fetch(`/nonconforming/api/items/${id}/label`);
    const data = await res.json();
    if (!data.ok) return;
    state.labelItem = { id, zpl: data.zpl, number: data.number };
    $("nc-label-number").textContent = data.number;
    $("nc-label-status").textContent = window.BrowserPrint
      ? "Zebra Browser Print detected."
      : "Zebra Browser Print not detected on this machine — download the .zpl file instead and print it from Zebra's own utility.";
    $("nc-label-download").href = `/nonconforming/api/items/${id}/label?download=1`;
    $("nc-label-modal").classList.remove("hidden");
  }

  function closeLabelModal() {
    $("nc-label-modal").classList.add("hidden");
    state.labelItem = null;
  }

  function bindLabelModal() {
    const closeBtn = $("nc-label-close");
    if (closeBtn) closeBtn.addEventListener("click", closeLabelModal);
    const modal = $("nc-label-modal");
    if (modal) modal.addEventListener("click", e => { if (e.target === modal) closeLabelModal(); });

    const printBtn = $("nc-label-print");
    if (printBtn) printBtn.addEventListener("click", () => {
      const statusEl = $("nc-label-status");
      if (!state.labelItem) return;
      if (!window.BrowserPrint) {
        statusEl.textContent = "Zebra Browser Print isn't running on this machine. Use the Download .zpl link and print it from Zebra's utility, or install Browser Print from Zebra's site.";
        return;
      }
      // Standard Zebra Browser Print JS SDK flow: grab the default printer,
      // send raw ZPL to it. See https://www.zebra.com/us/en/products/software/barcode-printers/link-os/browser-print.html
      window.BrowserPrint.getDefaultDevice("printer", device => {
        if (!device) {
          statusEl.textContent = "No default Zebra printer found in Browser Print.";
          return;
        }
        device.send(state.labelItem.zpl, () => {
          statusEl.textContent = `Sent ${state.labelItem.number} to ${device.name}.`;
        }, err => {
          statusEl.textContent = "Print failed: " + err;
        });
      }, err => {
        statusEl.textContent = "Could not reach Browser Print: " + err;
      });
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
    bindLabelModal();
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
