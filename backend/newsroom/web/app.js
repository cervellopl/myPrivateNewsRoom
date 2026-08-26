/* myPrivateNewsRoom - web client */
const state = {
  view: "feed",
  offset: 0,
  limit: 30,
  plugins: [],
  sources: [],
  token: localStorage.getItem("newsroom_token") || "",
  // ?cols=5 wins over the stored preference, so a layout can be linked to
  // full-width board is the default; the toggle stores an explicit choice
  fullwidth: (localStorage.getItem("newsroom_fullwidth") ?? "1") === "1",
  bookmarkFilter: null,
  notify: localStorage.getItem("newsroom_notify") === "1",
  // auto-refresh cadence in seconds; 0 turns it off
  refreshEvery: Number(localStorage.getItem("newsroom_refresh") ?? 60),
  lastCheck: null,
  checking: false,
  items: [],          // what the board is currently showing
  categories: [],     // bookmark categories
  columnEls: [],      // the masonry column elements
  columns: clampColumns(
    new URLSearchParams(location.search).get("cols") ||
      localStorage.getItem("newsroom_columns"),
  ),
};

/** Cards per row: 1-6, kept in this browser. */
function clampColumns(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 1 && n <= 6 ? Math.round(n) : 3;
}

/** Handle for the auto-refresh timer; declared here because bootstrap, further
 *  down this file, starts it before the auto-refresh section is reached. */
let refreshTimer = null;

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const { dataset, ...rest } = props;
  const node = Object.assign(document.createElement(tag), rest);
  // dataset is a read-only DOMStringMap, so its keys are copied one by one.
  Object.entries(dataset || {}).forEach(([k, v]) => (node.dataset[k] = v));
  kids.flat().forEach((k) => node.append(k));
  return node;
};

function toast(message, ms = 2600) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => box.classList.remove("show"), ms);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["X-API-Token"] = state.token;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(`/api${path}`, { ...options, headers });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : res.statusText);
  return data;
}

const fmtDate = (iso) => {
  if (!iso) return "no date";
  const d = new Date(iso), diff = (Date.now() - d) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
};
const fmtEvery = (s) => (s % 3600 === 0 ? `${s / 3600} h` : s % 60 === 0 ? `${s / 60} min` : `${s} s`);

/** Stable colour per source: same category, same hue on every reload. */
function hueFor(name) {
  let hash = 0;
  for (const ch of String(name)) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  // nudge off the exact accent red so source colours stay distinct from it
  return (hash + 17) % 360;
}

function applyFullWidth(on) {
  state.fullwidth = !!on;
  localStorage.setItem("newsroom_fullwidth", state.fullwidth ? "1" : "0");
  document.body.classList.toggle("fullwidth", state.fullwidth);
  if (state.items.length) repackBoard();
  $("#fullwidth").checked = state.fullwidth;
  const setting = $("#fullwidth-setting");
  if (setting) setting.checked = state.fullwidth;
}

/** Colour key for the sources currently in the feed; click to filter. */
function renderLegend() {
  const box = $("#legend");
  box.replaceChildren();
  if (!state.sources.length) return;
  state.sources.forEach((source) => {
    const button = el("button", {
      type: "button",
      onclick: () => {
        vmSetFilter(state.sourceFilter === source.id ? "" : String(source.id));
      },
    },
      el("span", { className: "dot" }),
      el("span", { textContent: source.name }),
      el("span", { className: "count", textContent: source.news_count }),
    );
    button.style.setProperty("--hue", hueFor(source.name));
    button.setAttribute("aria-pressed", String(state.sourceFilter === source.id));
    box.append(button);
  });
}

function vmSetFilter(value) {
  $("#filter-source").value = value;
  state.sourceFilter = value ? Number(value) : null;
  renderLegend();
  loadFeed();
}

/* ---------- desktop notifications ---------- */

/**
 * Notifications need a secure context: https, or http on localhost. Opened as
 * http://<lan-ip>:8080 the browser hides the API entirely, so the toggle
 * explains that rather than silently doing nothing.
 */
function notificationsSupported() {
  return typeof Notification !== "undefined" && window.isSecureContext;
}

function notificationState() {
  if (typeof Notification === "undefined") return "unsupported";
  if (!window.isSecureContext) return "insecure";
  return Notification.permission;   // "granted" | "denied" | "default"
}

async function enableNotifications() {
  if (!notificationsSupported()) return false;
  const permission = Notification.permission === "granted"
    ? "granted"
    : await Notification.requestPermission();
  const on = permission === "granted";
  state.notify = on;
  localStorage.setItem("newsroom_notify", on ? "1" : "0");
  return on;
}

/**
 * One toast per new item, up to a few, then a single summary - a busy poll
 * should not bury the desktop under twenty notifications.
 */
function notifyNewItems(items) {
  if (!state.notify || notificationState() !== "granted" || !items.length) return;

  const MAX = 3;
  items.slice(0, MAX).forEach((item) => {
    const toast = new Notification(item.title, {
      body: item.source + (item.summary ? ` — ${item.summary.slice(0, 120)}` : ""),
      icon: item.image || undefined,
      tag: `newsroom-${item.id}`,   // replaces itself rather than stacking
    });
    toast.onclick = () => {
      window.focus();
      window.open(item.link, "_blank", "noopener");
      toast.close();
    };
  });

  if (items.length > MAX) {
    new Notification(`${items.length - MAX} more new item(s)`, {
      body: "myPrivateNewsRoom",
      tag: "newsroom-summary",
    });
  }
}

/* ---------- masonry board ---------- */

/** Columns actually usable at this window width (narrow screens step down). */
function effectiveColumns() {
  const width = window.innerWidth;
  if (width <= 560) return 1;
  if (width <= 820) return Math.min(state.columns, 2);
  if (width <= 1100) return Math.min(state.columns, 3);
  return state.columns;
}

function buildColumns() {
  const grid = $("#news-grid");
  grid.replaceChildren();
  state.columnEls = Array.from({ length: effectiveColumns() }, () => {
    const column = el("div", { className: "masonry-col" });
    grid.append(column);
    return column;
  });
}

/** The shortest column right now - cards go there, which is what packs them. */
function shortestColumn() {
  return state.columnEls.reduce((a, b) => (b.offsetHeight < a.offsetHeight ? b : a));
}

function placeCards(items) {
  if (!state.columnEls.length) buildColumns();
  items.forEach((item) => shortestColumn().append(newsCard(item)));
}

/**
 * Swap one card in place, keeping every other card and the scroll position.
 * Used after bookmarking, where only that item changed.
 */
function refreshCard(item) {
  const existing = $(`#news-grid .card[data-id="${item.id}"]`);
  const index = state.items.findIndex((i) => i.id === item.id);
  if (index >= 0) state.items[index] = item;
  if (existing) existing.replaceWith(newsCard(item));
}

/**
 * Pull only what is new and slide it in at the top, instead of rebuilding the
 * board. Called by the poll timer and after an explicit fetch, so reading is
 * never interrupted by the page rearranging itself.
 */
async function loadNewItems() {
  if (state.loading || state.offset === 0) return 0;
  // the same filters the feed is currently showing, read where loadFeed reads them
  const params = new URLSearchParams({ limit: state.limit, offset: 0 });
  const q = $("#search").value.trim();
  if (q) params.set("q", q);
  if ($("#filter-source").value) params.set("source_id", $("#filter-source").value);
  if ($("#only-images").checked) params.set("with_image", "true");

  let page;
  try {
    page = await api(`/news?${params}`);
  } catch {
    return 0;   // a failed poll is not worth interrupting the reader over
  }

  const known = new Set(state.items.map((i) => i.id));
  const fresh = page.items.filter((i) => !known.has(i.id));
  if (!fresh.length) {
    state.total = page.total;
    return 0;
  }

  // newest last, so each prepend leaves the newest card at the top
  [...fresh].reverse().forEach((item) => {
    const card = newsCard(item);
    card.classList.add("card--new");
    const column = state.columnEls[0] || $("#news-grid");
    column.prepend(card);
  });
  state.items = fresh.concat(state.items);
  state.total = page.total;
  state.offset += fresh.length;
  updateNewBadge(fresh.length);
  notifyNewItems(fresh);
  return fresh.length;
}

/** A quiet "3 new" pill rather than a jump or a toast for every poll. */
function updateNewBadge(count) {
  const badge = $("#new-badge");
  const pending = Number(badge.dataset.count || 0) + count;
  badge.dataset.count = pending;
  badge.textContent = `${pending} new`;
  badge.classList.remove("hidden");
}

function clearNewBadge() {
  const badge = $("#new-badge");
  badge.dataset.count = 0;
  badge.classList.add("hidden");
}

/** Re-pack everything, e.g. after the column count or window width changes. */
function repackBoard() {
  buildColumns();
  placeCards(state.items);
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.columnEls.length !== effectiveColumns()) repackBoard();
  }, 200);
});

function applyColumns(value) {
  state.columns = clampColumns(value);
  localStorage.setItem("newsroom_columns", state.columns);
  const grid = $("#news-grid");
  grid.style.setProperty("--cols", state.columns);
  grid.dataset.cols = state.columns;
  if (state.items.length) repackBoard();
  $("#columns").value = state.columns;
  const slider = $("#columns-setting");
  if (slider) {
    slider.value = state.columns;
    $("#columns-value").textContent = state.columns;
  }
}

/* ---------- feed ---------- */
function newsCard(item) {
  // Cards without a picture drop the image area entirely rather than showing an
  // empty box - sources like Wyborcza publish many items with no photo at all.
  const thumb = item.image
    ? el("a", { href: item.link, target: "_blank", rel: "noopener noreferrer" },
        el("img", { className: "thumb", src: item.image, loading: "lazy", alt: "",
                    onerror: (e) => e.target.closest("a")?.remove() }))
    : "";

  const card = el("article", { className: item.image ? "card" : "card card--textonly" },
    thumb,
    el("div", { className: "body" },
      el("h3", {}, el("a", { href: item.link, target: "_blank", rel: "noopener noreferrer", textContent: item.title })),
      item.summary ? el("p", { textContent: item.summary }) : "",
      el("div", { className: "meta" },
        el("span", { className: "badge", textContent: item.source }),
        // sources that publish no date (scraped listings) show when we first
        // saw the item instead, and say so on hover
        el("span", {
          textContent: item.date ? fmtDate(item.date) : `seen ${fmtDate(item.fetched_at)}`,
          title: item.date
            ? new Date(item.date).toLocaleString()
            : "This source publishes no date; showing when it was first collected",
        }),
      ),
    ),
  );
  card.append(cardActions(item));
  card.dataset.id = item.id;
  // colour the card by its source, so categories are separable at a glance
  card.dataset.hue = "";
  card.style.setProperty("--hue", hueFor(item.source));
  return card;
}

/** Save / export controls shown under every card. */
function cardActions(item, { bookmark = false } = {}) {
  const saveButton = el("button", {
    type: "button",
    className: item.bookmark_id ? "saved" : "",
    textContent: item.bookmark_id ? `★ ${item.bookmark_category || "Saved"}` : "☆ Save",
    title: item.bookmark_id ? "Change category, or remove" : "Save to a category",
    onclick: (e) => openCategoryMenu(e.currentTarget, item),
  });

  const base = bookmark ? `/bookmarks/${item.id}` : `/news/${item.id}`;
  return el("div", { className: "actions" },
    bookmark ? "" : saveButton,
    el("button", { type: "button", textContent: "PDF",
      title: "Download the whole article page as a PDF",
      onclick: (e) => downloadExport(e.currentTarget, base, "pdf", item.title) }),
    el("button", { type: "button", textContent: "JPG",
      title: "Download the whole article page as one tall image",
      onclick: (e) => downloadExport(e.currentTarget, base, "jpg", item.title) }),
  );
}

/**
 * Fetch an export and hand it to the browser. Rendering a page can take the
 * better part of a minute the first time, so the button reports progress
 * instead of the tab appearing to hang on a plain link.
 */
async function downloadExport(button, base, fmt, title) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "rendering…";
  toast(`Rendering ${fmt.toUpperCase()} — the first time takes a while`, 6000);
  try {
    const res = await fetch(`/api${base}/export.${fmt}`);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || res.statusText);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const name = (title || "article").replace(/[^\w\s-]/g, "").trim().slice(0, 70)
      .replace(/\s+/g, "-") || "article";
    const link = el("a", { href: url, download: `${name}.${fmt}` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(`${fmt.toUpperCase()} downloaded`);
  } catch (err) {
    toast(`Export failed: ${err.message}`, 5000);
  }
  button.disabled = false;
  button.textContent = original;
}

/** Small popover listing the categories an item can be filed under. */
function openCategoryMenu(button, item) {
  document.querySelector(".menu")?.remove();
  const menu = el("div", { className: "menu" });
  const rect = button.getBoundingClientRect();
  menu.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 220)}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 6}px`;

  const save = async (categoryId) => {
    menu.remove();
    try {
      const bookmark = await api("/bookmarks", { method: "POST",
        body: { news_id: item.id, category_id: categoryId } });
      toast(bookmark.category ? `Saved to ${bookmark.category}` : "Saved");
      refreshCard({ ...item,
        bookmark_id: bookmark.id, bookmark_category: bookmark.category });
    } catch (err) { toast(err.message); }
  };

  state.categories.forEach((cat) =>
    menu.append(el("button", { type: "button",
      textContent: `${item.bookmark_category === cat.name ? "★ " : ""}${cat.name}`,
      onclick: () => save(cat.id) })));
  menu.append(el("button", { type: "button", textContent: "No category",
    onclick: () => save(null) }));
  menu.append(el("hr"));
  menu.append(el("button", { type: "button", textContent: "＋ New category…",
    onclick: async () => {
      menu.remove();
      const name = prompt("Category name");
      if (!name) return;
      try {
        const cat = await api("/bookmark-categories", { method: "POST", body: { name } });
        state.categories.push(cat);
        await save(cat.id);   // patches the card, no board rebuild
      } catch (err) { toast(err.message); }
    } }));

  if (item.bookmark_id) {
    menu.append(el("hr"));
    menu.append(el("button", { type: "button", textContent: "✕ Remove bookmark",
      onclick: async () => {
        menu.remove();
        try {
          await api(`/bookmarks/${item.bookmark_id}`, { method: "DELETE" });
          toast("Removed");
          refreshCard({ ...item, bookmark_id: null, bookmark_category: null });
        } catch (err) { toast(err.message); }
      } }));
  }

  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", function close(e) {
    if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); }
  }), 0);
}

async function loadFeed(append = false) {
  if (!append) state.offset = 0;
  const params = new URLSearchParams({ limit: state.limit, offset: state.offset });
  const q = $("#search").value.trim();
  if (q) params.set("q", q);
  if ($("#filter-source").value) params.set("source_id", $("#filter-source").value);
  if ($("#only-images").checked) params.set("with_image", "true");

  const page = await api(`/news?${params}`);
  if (append) {
    state.items = state.items.concat(page.items);
    placeCards(page.items);          // keep what is on screen, add below it
  } else {
    state.items = page.items;
    repackBoard();
    clearNewBadge();
  }
  state.offset += page.items.length;
  $("#news-empty").classList.toggle("hidden", page.total > 0);
  $("#load-more").classList.toggle("hidden", state.offset >= page.total);
}

/* ---------- bookmarks ---------- */

async function loadBookmarks() {
  state.categories = await api("/bookmark-categories");
  renderCategoryChips();

  const params = new URLSearchParams();
  const q = $("#bm-search").value.trim();
  if (q) params.set("q", q);
  if (state.bookmarkFilter) params.set("category_id", state.bookmarkFilter);

  const bookmarks = await api(`/bookmarks?${params}`);
  const grid = $("#bm-grid");
  grid.replaceChildren();
  bookmarks.forEach((bm) => grid.append(bookmarkCard(bm)));
  $("#bm-empty").classList.toggle("hidden", bookmarks.length > 0);
  $("#bm-count").textContent =
    `${bookmarks.length} saved${state.bookmarkFilter ? " in this category" : ""}`;
}

function renderCategoryChips() {
  const box = $("#cat-legend");
  box.replaceChildren();
  const all = el("button", { type: "button", textContent: "All",
    onclick: () => { state.bookmarkFilter = null; loadBookmarks(); } });
  all.setAttribute("aria-pressed", String(!state.bookmarkFilter));
  box.append(all);

  state.categories.forEach((cat) => {
    const chip = el("button", { type: "button" },
      el("span", { className: "dot" }),
      el("span", { textContent: cat.name }),
      el("span", { className: "count", textContent: cat.bookmark_count }),
    );
    chip.style.setProperty("--hue", hueFor(cat.name));
    chip.setAttribute("aria-pressed", String(state.bookmarkFilter === cat.id));
    chip.onclick = () => {
      state.bookmarkFilter = state.bookmarkFilter === cat.id ? null : cat.id;
      loadBookmarks();
    };
    chip.oncontextmenu = async (e) => {   // right-click to rename or delete
      e.preventDefault();
      const name = prompt(`Rename "${cat.name}" (empty to delete it)`, cat.name);
      if (name === null) return;
      try {
        if (name.trim() === "") {
          if (!confirm(`Delete "${cat.name}"? Its bookmarks are kept, just uncategorised.`)) return;
          await api(`/bookmark-categories/${cat.id}`, { method: "DELETE" });
        } else {
          await api(`/bookmark-categories/${cat.id}`, { method: "PATCH", body: { name } });
        }
        loadBookmarks();
      } catch (err) { toast(err.message); }
    };
    box.append(chip);
  });
  box.append(el("span", { className: "hint", textContent: "right-click a category to rename or delete" }));
}

function bookmarkCard(bm) {
  const thumb = bm.image
    ? el("a", { href: bm.link, target: "_blank", rel: "noopener noreferrer" },
        el("img", { className: "thumb", src: bm.image, loading: "lazy", alt: "",
                    onerror: (e) => e.target.closest("a")?.remove() }))
    : "";

  const card = el("article", { className: bm.image ? "card" : "card card--textonly" },
    thumb,
    el("div", { className: "body" },
      el("h3", {}, el("a", { href: bm.link, target: "_blank",
                             rel: "noopener noreferrer", textContent: bm.title })),
      bm.note ? el("p", { className: "note", textContent: bm.note }) : "",
      el("div", { className: "meta" },
        el("span", { className: "badge", textContent: bm.category || "Uncategorised" }),
        el("span", { textContent: bm.source || "" }),
      ),
    ),
    el("div", { className: "actions" },
      el("button", { type: "button", textContent: "Move…",
        onclick: (e) => openMoveMenu(e.currentTarget, bm) }),
      el("button", { type: "button", textContent: "Note…",
        onclick: async () => {
          const note = prompt("Note", bm.note || "");
          if (note === null) return;
          try {
            await api(`/bookmarks/${bm.id}`, { method: "PATCH", body: { note } });
            loadBookmarks();
          } catch (err) { toast(err.message); }
        } }),
      el("button", { type: "button", textContent: "PDF",
        onclick: (e) => downloadExport(e.currentTarget, `/bookmarks/${bm.id}`, "pdf", bm.title) }),
      el("button", { type: "button", textContent: "JPG",
        onclick: (e) => downloadExport(e.currentTarget, `/bookmarks/${bm.id}`, "jpg", bm.title) }),
      el("button", { type: "button", className: "danger", textContent: "✕",
        title: "Remove this bookmark",
        onclick: async () => {
          if (!confirm("Remove this bookmark?")) return;
          try {
            await api(`/bookmarks/${bm.id}`, { method: "DELETE" });
            loadBookmarks();
          } catch (err) { toast(err.message); }
        } }),
    ),
  );
  card.dataset.hue = "";
  card.style.setProperty("--hue", hueFor(bm.category || bm.source || "x"));
  return card;
}

function openMoveMenu(button, bm) {
  document.querySelector(".menu")?.remove();
  const menu = el("div", { className: "menu" });
  const rect = button.getBoundingClientRect();
  menu.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 220)}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 6}px`;

  const move = async (categoryId) => {
    menu.remove();
    try {
      await api(`/bookmarks/${bm.id}`, { method: "PATCH", body: { category_id: categoryId } });
      loadBookmarks();
    } catch (err) { toast(err.message); }
  };
  state.categories.forEach((cat) =>
    menu.append(el("button", { type: "button", textContent: cat.name,
      onclick: () => move(cat.id) })));
  menu.append(el("hr"));
  menu.append(el("button", { type: "button", textContent: "No category",
    onclick: () => move(null) }));

  document.body.append(menu);
  setTimeout(() => document.addEventListener("click", function close(e) {
    if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); }
  }), 0);
}

/* ---------- sources ---------- */
function configFields(pluginName) {
  const plugin = state.plugins.find((p) => p.name === pluginName);
  const box = $("#src-config");
  box.replaceChildren();
  (plugin?.config_spec || []).forEach((spec) => {
    const id = `cfg-${spec.key}`;
    const input = el("input", {
      id,
      type: spec.type === "int" ? "number" : spec.type === "bool" ? "checkbox"
           : spec.type === "secret" ? "password" : "text",
      placeholder: spec.placeholder || "",
      dataset: { key: spec.key, type: spec.type || "string" },
    });
    if (spec.type === "bool") input.checked = spec.default !== false;
    else if (spec.default !== undefined) input.value = spec.default;
    box.append(el("div", { className: "field" },
      el("label", { htmlFor: id, textContent: spec.label + (spec.required ? " *" : "") }),
      input));
  });
}

function readConfig() {
  const out = {};
  $("#src-config").querySelectorAll("input").forEach((input) => {
    const { key, type } = input.dataset;
    if (type === "bool") out[key] = input.checked;
    else if (type === "int") { if (input.value !== "") out[key] = Number(input.value); }
    else if (input.value.trim() !== "") out[key] = input.value.trim();
  });
  return out;
}

/**
 * Refresh the sources table without rebuilding it: only rows whose status,
 * last run or item count actually changed are replaced, so a table being
 * watched does not flicker every few seconds.
 */
async function updateSourceRows() {
  const fresh = await api("/sources");
  const previous = new Map(state.sources.map((s) => [s.id, s]));
  const body = $("#sources-body");

  const changed = (a, b) => !a
    || a.last_run_at !== b.last_run_at || a.last_status !== b.last_status
    || a.news_count !== b.news_count || a.enabled !== b.enabled;

  // a source added or removed elsewhere changes the table's shape: redraw once
  if (fresh.length !== state.sources.length) {
    state.sources = fresh;
    body.replaceChildren();
    fresh.forEach((s) => body.append(sourceRow(s)));
  } else {
    fresh.forEach((source) => {
      if (!changed(previous.get(source.id), source)) return;
      const row = body.querySelector(`tr[data-id="${source.id}"]`);
      if (row) row.replaceWith(sourceRow(source));
    });
    state.sources = fresh;
  }
  await fillSourceFilter();
  renderLegend();
}

/** Redraw a single source row after it was polled, leaving the table alone. */
async function refreshSourceRow(sourceId) {
  const fresh = await api(`/sources/${sourceId}`);
  const index = state.sources.findIndex((s) => s.id === sourceId);
  if (index >= 0) state.sources[index] = fresh;
  const row = document.querySelector(`#sources-body tr[data-id="${sourceId}"]`);
  if (row) row.replaceWith(sourceRow(fresh));
  renderLegend();
}

async function loadSources() {
  // the Add-a-source form lives on this tab, so its plugin list must be ready
  // even when the Plugins tab was never opened
  if (!state.plugins.length) state.plugins = await api("/plugins");
  fillPluginSelect();
  state.sources = await api("/sources");
  const body = $("#sources-body");
  body.replaceChildren();
  state.sources.forEach((s) => body.append(sourceRow(s)));
  await fillSourceFilter();
  renderLegend();
}

function sourceRow(s) {
  const status = el("span", {
    className: s.last_status === "error" ? "status-error" : "status-ok",
    textContent: s.last_run_at ? `${fmtDate(s.last_run_at)} · ${s.last_status}` : "never",
    title: s.last_error || "",
  });
  return el("tr", { dataset: { id: s.id } },
    el("td", {}, el("b", { textContent: s.name }),
      s.enabled ? "" : el("span", { className: "hint", textContent: " (disabled)" })),
    el("td", { textContent: s.plugin }),
    el("td", { textContent: fmtEvery(s.effective_interval_seconds) }),
    el("td", {}, status),
    el("td", { textContent: s.news_count }),
    el("td", {},
      el("button", { className: "btn", textContent: "Fetch",
        onclick: async (e) => {
          e.target.disabled = true;
          try {
            const r = await api(`/sources/${s.id}/refresh`, { method: "POST" });
            toast(`${r.name}: ${r.added} new of ${r.fetched}${r.error ? " — " + r.error : ""}`);
            await refreshSourceRow(s.id);   // just this row
            e.target.disabled = false;
          } catch (err) { toast(err.message); e.target.disabled = false; }
        } }),
      " ",
      el("button", { className: "btn", textContent: s.enabled ? "Pause" : "Resume",
        onclick: async () => {
          await api(`/sources/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } });
          loadSources();
        } }),
      " ",
      el("button", { className: "btn danger", textContent: "Delete",
        onclick: async () => {
          if (!confirm(`Delete "${s.name}" and its stored news?`)) return;
          await api(`/sources/${s.id}`, { method: "DELETE" });
          toast("Source deleted");
          state.items = state.items.filter((i) => i.source_id !== s.id);
          await loadSources();            // the list itself changed
          repackBoard();                  // and its items left the board
        } }),
    ),
  );
}

async function fillSourceFilter() {
  const select = $("#filter-source");
  const current = select.value;
  select.replaceChildren(el("option", { value: "", textContent: "All sources" }));
  state.sources.forEach((s) =>
    select.append(el("option", { value: s.id, textContent: s.name })));
  select.value = current;
}

/* ---------- plugins ---------- */
async function loadPlugins() {
  state.plugins = await api("/plugins");
  const body = $("#plugins-body");
  body.replaceChildren();
  state.plugins.forEach((p) => {
    body.append(el("tr", {},
      el("td", {}, el("b", { textContent: p.name }),
        p.loaded ? "" : el("div", { className: "status-error", textContent: p.load_error || "not loaded" })),
      el("td", { textContent: p.description || "" }),
      el("td", { textContent: p.version || "" }),
      el("td", { textContent: p.sources_using }),
      el("td", {}, el("button", { className: "btn danger", textContent: "Remove",
        onclick: async () => {
          if (!confirm(`Remove plugin "${p.name}"?`)) return;
          try {
            await api(`/plugins/${p.name}`, { method: "DELETE" });
            toast("Plugin removed");
            loadPlugins();
          } catch (err) { toast(err.message); }
        } })),
    ));
  });

  fillPluginSelect();
}

/** Fill the "Add a source" plugin picker; needed wherever that form is shown. */
function fillPluginSelect() {
  const select = $("#src-plugin");
  const current = select.value;
  select.replaceChildren();
  state.plugins.filter((p) => p.loaded).forEach((p) =>
    select.append(el("option", { value: p.name, textContent: `${p.name} — ${p.description || ""}` })));
  if (current && state.plugins.some((p) => p.name === current)) select.value = current;
  configFields(select.value);
}

/* ---------- settings ---------- */
async function loadSettings() {
  const [settings, status] = await Promise.all([api("/settings"), api("/status")]);
  $("#poll-interval").value = settings.poll_interval_seconds;
  $("#poll-hint").textContent =
    `Every source is polled at this rate unless it overrides it. Minimum ${status.min_poll_interval_seconds} s. ` +
    `Auth ${status.auth_required ? "required" : "disabled"}.`;
  $("#status-out").textContent = JSON.stringify(status, null, 2);
  $("#api-token").value = state.token;
  $("#refresh-setting").value = String(state.refreshEvery);
  renderNotifyState();
}

/* ---------- wiring ---------- */
function showView(name, updateHash = true) {
  state.view = name;
  if (updateHash && location.hash.slice(1) !== name) location.hash = name;
  document.querySelectorAll("nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  ["feed", "bookmarks", "sources", "plugins", "settings"].forEach((v) =>
    $(`#view-${v}`).classList.toggle("hidden", v !== name));
  ({ feed: loadFeed, bookmarks: loadBookmarks, sources: loadSources,
     plugins: loadPlugins, settings: loadSettings }[name])()
    .catch((err) => toast(err.message));
}

document.querySelectorAll("nav button").forEach((b) =>
  b.onclick = () => showView(b.dataset.view));

const VIEWS = ["feed", "bookmarks", "sources", "plugins", "settings"];
window.onhashchange = () => {
  const name = location.hash.slice(1);
  if (VIEWS.includes(name) && name !== state.view) showView(name, false);
};

let searchTimer;
$("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadFeed(), 300); };
$("#filter-source").onchange = (e) => vmSetFilter(e.target.value);
$("#only-images").onchange = () => loadFeed();
$("#load-more").onclick = () => loadFeed(true);

// the pill scrolls back to the newest cards and clears the "new" marks
$("#new-badge").onclick = () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
  document.querySelectorAll(".card--new").forEach((c) => c.classList.remove("card--new"));
  clearNewBadge();
};
$("#columns").onchange = (e) => applyColumns(e.target.value);

let bmSearchTimer;
$("#bm-search").oninput = () => {
  clearTimeout(bmSearchTimer);
  bmSearchTimer = setTimeout(() => loadBookmarks().catch((e) => toast(e.message)), 300);
};
$("#cat-add").onclick = async () => {
  const name = $("#cat-name").value.trim();
  if (!name) return toast("Give the category a name");
  try {
    await api("/bookmark-categories", { method: "POST", body: { name } });
    $("#cat-name").value = "";
    toast(`Category "${name}" added`);
    loadBookmarks();
  } catch (err) { toast(err.message); }
};
$("#refresh-every").onchange = (e) => {
  applyAutoRefresh(e.target.value);
  if (state.refreshEvery) autoRefreshTick();   // act on the new setting at once
};
$("#fullwidth").onchange = (e) => applyFullWidth(e.target.checked);
$("#fullwidth-setting").onchange = (e) => applyFullWidth(e.target.checked);
$("#columns-setting").oninput = (e) => applyColumns(e.target.value);

$("#refresh-all").onclick = async (e) => {
  e.target.disabled = true;
  e.target.textContent = "Fetching…";
  try {
    const r = await api("/refresh", { method: "POST" });
    const added = r.results.reduce((sum, x) => sum + x.added, 0);
    const shown = await loadNewItems();
    toast(`${r.sources} source(s) polled, ${added} new item(s)` +
          (shown ? `, ${shown} added above` : ""));
  } catch (err) { toast(err.message); }
  e.target.disabled = false;
  e.target.textContent = "Fetch now";
};

$("#src-plugin").onchange = (e) => configFields(e.target.value);

$("#src-save").onclick = async () => {
  const name = $("#src-name").value.trim();
  if (!name) return toast("Give the source a name");
  const interval = $("#src-interval").value;
  try {
    await api("/sources", { method: "POST", body: {
      name, plugin: $("#src-plugin").value, config: readConfig(),
      enabled: true, interval_seconds: interval ? Number(interval) : null,
    } });
    $("#src-name").value = ""; $("#src-interval").value = "";
    toast("Source added");
    loadSources();
  } catch (err) { toast(err.message); }
};

$("#src-test").onclick = async (e) => {
  e.target.disabled = true;
  const out = $("#src-test-out");
  out.classList.remove("hidden");
  out.textContent = "Running…";
  try {
    const r = await api(`/plugins/${$("#src-plugin").value}/test`, { method: "POST", body: readConfig() });
    out.textContent = `${r.count} item(s) found:\n\n` + r.items.map((i) =>
      `• ${i.title}\n  ${i.link}\n  image: ${i.image || "—"}`).join("\n");
  } catch (err) { out.textContent = "Failed: " + err.message; }
  e.target.disabled = false;
};

$("#plugin-upload").onclick = async () => {
  const file = $("#plugin-file").files[0];
  if (!file) return toast("Choose a .py file first");
  const form = new FormData();
  form.append("file", file);
  try {
    const p = await api("/plugins", { method: "POST", body: form });
    toast(`Plugin "${p.name}" installed`);
    $("#plugin-file").value = "";
    loadPlugins();
  } catch (err) { toast("Rejected: " + err.message); }
};

$("#poll-save").onclick = async () => {
  try {
    const s = await api("/settings", { method: "PUT",
      body: { poll_interval_seconds: Number($("#poll-interval").value) } });
    toast(`Polling every ${fmtEvery(s.poll_interval_seconds)}`);
  } catch (err) { toast(err.message); }
};

function renderNotifyState() {
  const box = $("#notify-state");
  const toggle = $("#notify-toggle");
  const message = {
    unsupported: "this browser has no notification support",
    insecure: "blocked: open the app over https or on localhost",
    denied: "blocked in browser settings for this site",
    granted: state.notify ? "on" : "allowed — switch on to receive them",
    default: "permission not asked yet",
  }[notificationState()];
  box.textContent = message;
  toggle.checked = state.notify && notificationState() === "granted";
  toggle.disabled = ["unsupported", "insecure", "denied"].includes(notificationState());
}

$("#refresh-setting").onchange = (e) => applyAutoRefresh(e.target.value);

$("#notify-toggle").onchange = async (e) => {
  if (!e.target.checked) {
    state.notify = false;
    localStorage.setItem("newsroom_notify", "0");
  } else if (!(await enableNotifications())) {
    toast("The browser refused notification permission");
  } else {
    new Notification("myPrivateNewsRoom", { body: "Notifications are on." });
  }
  renderNotifyState();
};

$("#token-save").onclick = () => {
  state.token = $("#api-token").value.trim();
  localStorage.setItem("newsroom_token", state.token);
  toast("Token saved in this browser");
};

applyColumns(state.columns);
applyFullWidth(state.fullwidth);
applyAutoRefresh(state.refreshEvery);

const initialView = location.hash.slice(1);
if (["bookmarks", "sources", "plugins", "settings"].includes(initialView))
  showView(initialView, false);

api("/bookmark-categories").then((c) => { state.categories = c; }).catch(() => {});

api("/plugins").then((p) => { state.plugins = p; fillPluginSelect(); return api("/sources"); })
  .then((s) => { state.sources = s; renderLegend(); return fillSourceFilter(); })
  .then(() => loadFeed())
  .catch((err) => toast(err.message));

// Poll for new items only; the board is never rebuilt behind the reader's back.
/* ---------- auto-refresh ---------- */

/**
 * Poll for whatever the active view shows. The feed pulls in new items without
 * rebuilding the board; the Sources tab refreshes the status column, which goes
 * stale while you watch a fetch run.
 */
async function autoRefreshTick() {
  if (document.hidden || state.checking) return;
  state.checking = true;
  renderRefreshStatus();
  try {
    if (state.view === "feed") await loadNewItems();
    else if (state.view === "sources") await updateSourceRows();
    state.lastCheck = Date.now();
  } catch {
    // a failed poll is not worth interrupting the reader over
  }
  state.checking = false;
  renderRefreshStatus();
}

function applyAutoRefresh(seconds) {
  state.refreshEvery = Number(seconds) || 0;
  localStorage.setItem("newsroom_refresh", state.refreshEvery);
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = state.refreshEvery
    ? setInterval(autoRefreshTick, state.refreshEvery * 1000)
    : null;
  const select = $("#refresh-every");
  if (select) select.value = String(state.refreshEvery);
  const setting = $("#refresh-setting");
  if (setting) setting.value = String(state.refreshEvery);
  renderRefreshStatus();
}

/** "checking…" / "updated 12 s ago" / "auto-refresh off", in the toolbar. */
function renderRefreshStatus() {
  const box = $("#refresh-status");
  if (!box) return;
  if (!state.refreshEvery) {
    box.textContent = "auto-refresh off";
    return;
  }
  if (state.checking) {
    box.textContent = "checking…";
    return;
  }
  if (!state.lastCheck) {
    box.textContent = `every ${fmtEvery(state.refreshEvery)}`;
    return;
  }
  const seconds = Math.round((Date.now() - state.lastCheck) / 1000);
  box.textContent = seconds < 60
    ? `updated ${seconds}s ago`
    : `updated ${Math.round(seconds / 60)} min ago`;
}

// keep the "updated N ago" honest without polling the server for it
setInterval(renderRefreshStatus, 5000);

// catch up as soon as the tab is looked at again
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.refreshEvery) autoRefreshTick();
});
