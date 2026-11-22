// Recipe Cart front end. No framework: a small state object, view functions that
// build DOM with h(), and a render() that swaps the view. All text goes through
// textContent, so recipe text (from a model or a reel) can never inject markup.

// ---------- tiny helpers ----------
const app = document.getElementById("app");

function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "value") n.value = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2).toLowerCase(), v);
    else n.setAttribute(k, v === true && !k.startsWith("aria-") ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

// Icons are constant strings (never user data).
const ICONS = {
  paste: '<rect x="6" y="5" width="12" height="16" rx="2"/><path d="M9 5V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  instagram: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.2" cy="6.8" r="0.8" fill="currentColor"/>',
  text: '<path d="M5 5h14M5 10h14M5 15h9"/>',
  film: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/>',
  dish: '<path d="M4 15h16a8 8 0 0 1-16 0zM12 6v3M9 8l1 1M15 8l-1 1"/>',
};
function icon(name, size = 16) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", size); s.setAttribute("height", size); s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2.4");
  s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name];
  return s;
}
const arrowDown = () => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", 24); s.setAttribute("height", 28); s.setAttribute("viewBox", "0 0 24 28"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = '<path d="M12 3v18M5 14l7 9 7-9" fill="none" stroke="#101311" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>';
  s.setAttribute("class", "flow__arrow");
  return s;
};

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}

const REEL_RE = /instagram\.com\/(?:[A-Za-z0-9_.]+\/)?(?:reels?|p|tv)\/[A-Za-z0-9_-]{5,}/i;
const isReel = (s) => REEL_RE.test(s || "");

// ---------- state ----------
function loadPantry() {
  try { return JSON.parse(localStorage.getItem("rc.pantry") || "[]").filter((x) => typeof x === "string"); } catch { return []; }
}
function savePantry() {
  try { localStorage.setItem("rc.pantry", JSON.stringify([...state.have])); } catch { /* private mode: fine */ }
}

const state = {
  view: "start",          // "start" | "recipe"
  busy: false, busyText: "",
  input: "", caption: "", moreOpen: false,
  alert: null,            // { kind: "warn" | "error", text }
  cart: null,             // last /cart response
  servings: 4,
  have: new Set(loadPantry()),   // lowercase ingredient names you've said you have (remembered)
  swapped: new Map(),     // line key -> chosen swap suggestion
  forms: new Map(),       // line key -> add-to-catalogue form values
  listBusy: false,
  focus: null,            // selector to focus after the next render
  config: {},             // from /api/config
  provider: "catalogue",  // "catalogue" | "instamart" (demo)
  picker: new Set(),      // line keys whose product picker is open
  review: null,           // the order review screen's data
  reviewBusy: false,
  order: null,            // the placed / pending order
};
const keyOf = (name) => name.trim().toLowerCase();

// ---------- API ----------
async function request(path, options) {
  let res;
  try { res = await fetch(path, options); } catch (e) { return { ok: false, status: 0, error: "Could not reach the server. Is it running?" }; }
  const data = await res.json().catch(() => ({}));
  if (res.ok) return { ok: true, status: res.status, data };
  return { ok: false, status: res.status, detail: data.detail, error: errorText(data.detail, res.status) };
}
function errorText(d, status) {
  if (d && typeof d === "object" && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return d.map((e) => (e.loc || []).slice(1).join(".") + ": " + e.msg).join("; ");
  return typeof d === "string" ? d : `Something went wrong (${status}).`;
}

function cartBody(extra) {
  return JSON.stringify(Object.assign({ pantry: [...state.have], provider: state.provider }, extra));
}
function postCart(extra) {
  return request("/cart", { method: "POST", headers: { "Content-Type": "application/json" }, body: cartBody(extra) });
}

function showCart(data) {
  state.cart = data;
  state.servings = data.servings;
  state.alert = null;
  state.swapped.clear();
  state.picker.clear();
  state.provider = data.provider || "catalogue";
  if (state.view !== "recipe") { state.view = "recipe"; history.pushState({ view: "recipe" }, ""); }
}

async function build({ refresh = false } = {}) {
  const dish = state.input.trim() || (state.caption.trim() ? "pasted recipe" : "");
  if (!dish) { state.alert = { kind: "error", text: "Enter a dish name or paste an Instagram reel link." }; render(); return; }
  const reel = isReel(dish);
  state.busy = true; state.alert = null;
  state.busyText = reel ? "Reading the reel... this can take a few seconds." : "Working out the ingredients...";
  render();
  const r = await postCart({ dish, caption: state.caption.trim() || null, refresh });
  state.busy = false;
  if (r.ok) { showCart(r.data); window.scrollTo(0, 0); }
  else if (r.detail && r.detail.code === "needs_caption") {
    state.alert = { kind: "warn", text: r.detail.message }; state.moreOpen = true; state.focus = "#caption";
  } else state.alert = { kind: "error", text: r.error };
  render();
}

async function buildFromFile(file) {
  if (!file) return;
  state.busy = true; state.alert = null; state.busyText = "Reading the video... this can take 10-40 seconds.";
  render();
  const fd = new FormData();
  fd.append("file", file); fd.append("pantry", [...state.have].join(","));
  const r = await request("/cart/video", { method: "POST", body: fd });
  state.busy = false;
  if (r.ok) { showCart(r.data); window.scrollTo(0, 0); } else state.alert = { kind: "error", text: r.error };
  render();
}

// Re-price the saved recipe (new servings, new catalogue entry) without re-reading the source.
async function rebuild() {
  state.listBusy = true; render();
  const r = await postCart({ dish: state.cart.dish, recipe_key: state.cart.recipe_key, servings: state.servings });
  state.listBusy = false;
  if (r.ok) { const keep = new Map(state.swapped); showCart(r.data); reapplyPicks(keep, r.data.lines); }
  else toast(r.error);
  render();
}
const keepSwap = (line, k) => keyOf(line.name) === k && line.status === "open";

// After a re-price (new servings) keep what the customer chose, with the new pack counts.
function reapplyPicks(keep, lines) {
  for (const [k, pick] of keep) {
    const line = lines.find((l) => keyOf(l.name) === k);
    if (!line) continue;
    if (!pick.picked) { if (keepSwap(line, k)) state.swapped.set(k, pick); continue; }   // a catalogue swap
    const vid = pick.offer && pick.offer.variant_id;
    if (line.offer && line.offer.variant_id === vid) continue;                           // back to the suggestion
    const alt = (line.alternatives || []).find((a) => a.offer.variant_id === vid);
    if (alt) state.swapped.set(k, pickFromAlt(alt));
  }
}
function pickFromAlt(a) {
  return { product: a.label, packs: a.priced ? a.packs : 1, price: a.price, used_pct: a.priced ? a.used_pct : null,
           left_over: a.priced ? a.left_over : null, offer: a.offer, picked: true, unsure: !a.priced };
}

let servingsTimer;
function changeServings(n) {
  n = Math.min(100, Math.max(1, n));
  if (n === state.servings) return;
  state.servings = n; render();                 // number moves instantly
  clearTimeout(servingsTimer);
  servingsTimer = setTimeout(rebuild, 350);     // one request after the taps settle
}

async function createProduct(body) {
  const send = (token) => request("/products", {
    method: "POST",
    headers: Object.assign({ "Content-Type": "application/json" }, token ? { "X-Admin-Token": token } : {}),
    body: JSON.stringify(body),
  });
  const token = () => { try { return sessionStorage.getItem("adminToken") || ""; } catch { return ""; } };
  let r = await send(token());
  if (r.status === 401) {                        // the catalogue is token-protected: ask once per session
    const t = window.prompt("Admin token required to edit the catalogue:");
    if (!t) return { ok: false, error: "Admin token required." };
    try { sessionStorage.setItem("adminToken", t); } catch { /* ignore */ }
    r = await send(t);
  }
  return r;
}

// ---------- derived data ----------
export function summarize(lines, have, swapped) {
  const rows = lines.map((l) => {
    const key = keyOf(l.name);
    const sw = swapped.get(key);
    const eff = sw ? Object.assign({}, l, { status: "priced", product: sw.product, packs: sw.packs, price: sw.price, used_pct: sw.used_pct, left_over: sw.left_over, offer: sw.offer || l.offer, unsure: !!sw.unsure, swappedFor: sw }) : l;
    return Object.assign({}, eff, { key, isHave: have.has(key) });
  });
  let total = 0, priced = 0, open = 0, haveN = 0;
  for (const r of rows) {
    if (r.isHave) haveN++;
    else if (r.status === "priced") { priced++; total += r.price; }
    else open++;
  }
  return { rows, total: Math.round(total * 100) / 100, priced, open, haveN };
}

// What would go to the provider's cart: lines you're buying that have a chosen product.
export function orderItems(sum) {
  return sum.rows.filter((r) => !r.isHave && r.status === "priced" && r.offer).map((r) => ({ offer: r.offer, quantity: r.packs }));
}

export function listText(cart, sum) {
  const buy = sum.rows.filter((r) => !r.isHave && r.status === "priced");
  const open = sum.rows.filter((r) => !r.isHave && r.status !== "priced");
  const out = [`${cart.dish} - ${cart.servings} servings`, ""];
  if (buy.length) {
    out.push("BUY");
    for (const r of buy) out.push(`- ${r.product} x${r.packs}  (${r.name}: need ${r.estimated ? "~" : ""}${r.need})  ${r.price.toFixed(2)}`);
  }
  if (open.length) {
    out.push("", "NOT PRICED - buy elsewhere");
    for (const r of open) out.push(`- ${r.name} ${r.estimated ? "~" : ""}${r.need}`);
  }
  out.push("", `Total: ${sum.total.toFixed(2)}`);
  return out.join("\n");
}

async function copyList() {
  const text = listText(state.cart, summarize(state.cart.lines, state.have, state.swapped));
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h("textarea", { value: text, style: "position:fixed;opacity:0" }); document.body.append(ta); ta.select();
    try { document.execCommand("copy"); } catch { /* ignore */ } ta.remove();
  }
  toast("Shopping list copied");
}

function setHave(name, on) {
  const k = keyOf(name);
  if (on) { state.have.add(k); state.forms.delete(k); } else state.have.delete(k);
  savePantry(); render();
}

// ---------- start view ----------
function startView() {
  const reelNow = isReel(state.input);
  const input = h("input", {
    id: "src", class: "src__input", value: state.input, autocomplete: "off", spellcheck: "false",
    placeholder: "Paste a link or type a dish",
    onInput: (e) => { state.input = e.target.value; const hint = document.getElementById("src-hint"); if (hint) fillHint(hint); },
  });
  const fileInput = h("input", { type: "file", id: "video", accept: "video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm", class: "sr-only", onChange: (e) => buildFromFile(e.target.files[0]) });
  const hint = h("div", { id: "src-hint", class: "src__hint", "aria-live": "polite" });
  const fillHint = (el) => { el.replaceChildren(); if (isReel(state.input)) el.append(icon("check", 16), "Instagram reel link recognised"); };
  fillHint(hint);

  const pasteBtn = h("button", { type: "button", class: "btn", disabled: state.busy, onClick: async () => {
    try { const t = await navigator.clipboard.readText(); state.input = t.trim(); render(); state.focus = null; document.getElementById("go").focus(); }
    catch { toast("Couldn't read the clipboard. Press Ctrl+V in the box."); document.getElementById("src").focus(); }
  } }, icon("paste", 16), "Paste");

  const more = (state.moreOpen || state.caption) ? h("div", { class: "more" },
    h("label", { for: "caption", class: "src__label" }, "Paste the recipe text (a reel's caption, notes, anything)"),
    h("textarea", { id: "caption", placeholder: "Paste the caption or recipe here", value: state.caption, onInput: (e) => { state.caption = e.target.value; } }),
    h("small", {}, "Instagram often hides captions from other sites. Copy it from the reel and paste it here; with a link above, it's saved against that reel."),
    h("div", { class: "more__row" },
      h("button", { type: "submit", class: "btn btn--primary btn--small", disabled: state.busy }, "Build from this text"),
      h("label", { class: "btn btn--small", for: "video" }, "Or upload the video"),
      h("button", { type: "button", class: "btn btn--link", onClick: () => { state.moreOpen = false; state.caption = ""; render(); } }, "Close")),
    h("small", {}, "Videos are sent to Google Gemini to read the recipe from speech and on-screen text, and are not kept. Up to 100 MB; only upload videos you're allowed to use.")
  ) : null;

  const tryRow = h("div", { class: "try" },
    "Try",
    ...["fish molee", "dal tadka"].map((d) => h("button", { type: "button", class: "chip", disabled: state.busy, onClick: () => { state.input = d; build(); } }, d)),
    "or",
    h("label", { for: "video", class: "btn btn--link", style: "min-height:36px;display:inline-flex;align-items:center" }, "upload a video"), "/",
    h("button", { type: "button", class: "btn btn--link", onClick: () => { state.moreOpen = !state.moreOpen; state.focus = state.moreOpen ? "#caption" : null; render(); } }, "paste a caption"));

  const providerRow = state.config.demo_instamart ? h("div", { class: "src__provider" },
    h("span", { class: "src__label", id: "prov" }, "Prices from"),
    h("div", { class: "toggle toggle--wide", role: "group", "aria-labelledby": "prov" },
      ...[["catalogue", "Our catalogue"], ["instamart", "Instamart (demo)"]].map(([id, label]) =>
        h("button", { type: "button", class: "toggle__btn", "aria-pressed": String(state.provider === id), disabled: state.busy,
          onClick: () => { state.provider = id; try { localStorage.setItem("rc.provider", id); } catch { /* ignore */ } render(); } }, label)))) : null;

  const form = h("form", { class: "src", id: "src-form", novalidate: true, onSubmit: (e) => { e.preventDefault(); build(); } },
    h("label", { class: "src__label", for: "src" }, "Reel link or dish name"),
    h("div", { class: "src__bar" }, input, pasteBtn, h("button", { id: "go", type: "submit", class: "btn btn--primary", disabled: state.busy }, state.busy ? "Working..." : "Build my cart")),
    hint,
    state.alert ? h("div", { class: "alert" + (state.alert.kind === "error" ? " alert--error" : ""), role: "alert" }, state.alert.text) : null,
    fileInput, tryRow, providerRow, more,
    state.busy ? h("div", { class: "loading" }, h("div", { class: "loading__bar" }), h("div", { class: "loading__text" }, state.busyText)) : null);

  return [
    h("section", { class: "hero" },
      h("div", { class: "hero__left" },
        h("div", { class: "eyebrow" }, "Recipe → priced cart"),
        h("h1", { class: "big" }, "Saw it.", h("br"), "Cook it.", h("br"), h("span", { class: "big__mark" }, "Cart it.")),
        h("p", { class: "lede" }, "Paste a recipe reel or name a dish. You get a grocery cart priced from your own catalogue, minus whatever you already have."),
        form),
      h("div", { class: "hero__right" }, flowCard())),
    h("section", { class: "principles", "aria-label": "How it works" },
      principle("01", "Paste anything", "A reel link, a dish name, a caption or a video. Whatever you found the recipe in."),
      principle("02", "Tap what you already have", "No typing a pantry list. Tap an ingredient and it drops out of the cart."),
      principle("03", "Know what you'll use", "Every line shows how much of the pack the recipe needs and what's left over.")),
  ];
}
function principle(n, title, text) { return h("div", {}, h("b", { class: "num" }, n), h("h3", {}, title), h("p", {}, text)); }

function flowCard() {
  const gauge = (name, pct) => h("div", { class: "flow__gauge" },
    h("div", {}, h("span", {}, name), h("span", {}, `uses ${pct}%`)),
    h("div", { class: "gauge" }, h("div", { class: "gauge__fill", style: `width:${pct}%` })));
  return h("div", { class: "flow", "aria-label": "Example: a reel becomes a priced cart" },
    h("div", { class: "flow__card flow__source" },
      h("div", { class: "flow__thumb" }, icon("film", 22)),
      h("div", {}, h("div", { class: "eyebrow" }, "1 · Source"), h("div", { style: "font-weight:700;font-size:17px;margin-top:4px" }, "Instagram reel"),
        h("div", { style: "font-family:var(--mono);font-size:13px;color:var(--muted);margin-top:2px" }, "instagram.com/reel/..."))),
    arrowDown(),
    h("div", { class: "flow__card" },
      h("div", { class: "eyebrow" }, "2 · Tap what you have"),
      h("div", { class: "flow__title" }, "Creamy Garlic Butter Chicken Pasta"),
      h("div", { class: "flow__chips" }, h("span", {}, "penne"), h("span", {}, "garlic"), h("span", {}, "butter"), h("span", { class: "have" }, "salt · have it"))),
    arrowDown(),
    h("div", { class: "flow__card flow__card--dark" },
      h("div", { class: "flow__total" }, h("span", { class: "eyebrow" }, "3 · Your cart"), h("b", {}, "173.00")),
      gauge("Garlic 250g", 16), gauge("Butter 100g", 60),
      h("div", { class: "flow__foot" }, "See how much of every pack the recipe really uses.")));
}

// ---------- recipe view ----------
const SOURCES = {
  instagram: ["instagram", "Instagram reel"],
  text: ["text", "Pasted recipe text"],
  video: ["film", "Uploaded video"],
  dish: ["dish", "Dish name · a typical recipe"],
};

function recipeView() {
  const cart = state.cart;
  const sum = summarize(cart.lines, state.have, state.swapped);
  const [srcIcon, srcLabel] = SOURCES[cart.source] || SOURCES.dish;
  const anyEstimated = cart.lines.some((l) => l.estimated);

  const stepper = h("div", { class: "stepper" },
    h("span", { id: "sv", class: "stepper__label" }, "Servings"),
    h("div", { class: "stepper__box", role: "group", "aria-labelledby": "sv" },
      h("button", { type: "button", class: "stepper__btn", "aria-label": "Fewer servings", disabled: state.servings <= 1, onClick: () => changeServings(state.servings - 1) }, "−"),
      h("span", { class: "stepper__n", "aria-live": "polite" }, state.servings),
      h("button", { type: "button", class: "stepper__btn", "aria-label": "More servings", disabled: state.servings >= 100, onClick: () => changeServings(state.servings + 1) }, "+")));

  const legend = h("div", { class: "legend", "aria-hidden": "true" },
    anyEstimated ? h("span", {}, h("span", { class: "est" }, "~120 ml"), "estimated from the source") : null,
    h("span", {}, h("span", { class: "gauge" }, h("span", { class: "gauge__fill", style: "display:block;width:55%" })), "green is used, hatched is left over"),
    sum.open ? h("span", {}, h("span", { class: "swatch-open" }), "not in your catalogue yet") : null);

  const remembered = state.have.size;
  const hint = h("p", { class: "hint" }, "Tap ", h("strong", {}, "Have"), " on anything already in your kitchen. The total updates as you go.",
    remembered ? h("span", { style: "font-weight:500;color:var(--muted)" }, ` We remember ${remembered} thing${remembered === 1 ? "" : "s"} you have. `) : null,
    remembered ? h("button", { type: "button", class: "btn btn--link", style: "min-height:auto;padding:0", onClick: () => { state.have.clear(); savePantry(); render(); } }, "Forget") : null);

  const list = cart.lines.length
    ? h("ul", { class: "list", style: state.listBusy ? "opacity:.55;pointer-events:none" : null, "aria-busy": state.listBusy }, sum.rows.map(rowEl))
    : h("div", { class: "alert" }, "No ingredients found in that recipe.");

  return [
    h("nav", { class: "steps", "aria-label": "Progress" },
      h("span", { class: "done" }, "1 · Source ✓"), h("span", { class: "now" }, "2 · Check what you have"), h("span", { class: "next" }, "3 · Cart (live)")),
    h("section", { class: "head" },
      h("div", { class: "head__title" },
        h("h1", { class: "title" }, cart.dish),
        h("div", { class: "source" },
          h("span", { class: "source__chip" }, icon(srcIcon, 16), srcLabel),
          h("button", { type: "button", class: "btn btn--link", onClick: backToStart }, "Change source"))),
      stepper),
    legend,
    h("div", { class: "grid" }, h("div", { class: "grid__main" }, brandBar(cart), hint, list), cartPanel(sum, anyEstimated, cart)),
  ];
}

function brandBar(cart) {
  if (cart.provider !== "instamart") return null;
  return h("div", { class: "brandbar" },
    h("span", { class: "brandbar__logo", "aria-hidden": "true" }, "i"),   // placeholder: use Swiggy's official logo asset
    h("strong", {}, cart.provider_badge || "Powered by Instamart"),
    h("span", { class: "demo-pill" }, "Demo: simulated data"),
    h("span", { class: "brandbar__text" }, "Prices and availability come from Instamart."));
}

function backToStart() {
  clearTimeout(payTimer);
  state.view = "start"; state.alert = null; state.review = null; state.order = null;
  render(); window.scrollTo(0, 0);
}

function toggle(r) {
  return h("div", { class: "toggle", role: "group", "aria-label": `Buy or have ${r.name}` },
    h("button", { type: "button", class: "toggle__btn", "aria-pressed": String(!r.isHave), onClick: () => setHave(r.name, false) }, "Buy"),
    h("button", { type: "button", class: "toggle__btn", "aria-pressed": String(r.isHave), onClick: () => setHave(r.name, true) }, "Have"));
}

// The variants the customer can pick between for a line: the suggestion first, then alternatives.
function optionsFor(r) {
  const base = state.cart.lines.find((l) => keyOf(l.name) === r.key);
  if (!base || state.cart.provider === "catalogue") return [];
  const opts = [];
  if (base.status === "priced" && base.offer) {
    opts.push({ label: base.product, size_text: base.offer.size_text, packs: base.packs, price: base.price, used_pct: base.used_pct,
                left_over: base.left_over, offer: base.offer, priced: true, suggested: true });
  }
  return opts.concat(base.alternatives || []);
}

function pickerEl(r, opts) {
  const cur = r.offer && r.offer.variant_id;
  return h("div", { class: "alts", role: "list", "aria-label": `Choose a product for ${r.name}` },
    h("div", { class: "alts__title" }, r.status === "priced" ? "Choose another size or brand" : "Choose a product"),
    opts.map((o) => h("div", { class: "alt", role: "listitem" },
      h("div", { class: "alt__main" },
        h("div", { class: "alt__name" }, o.label, o.suggested ? h("span", { class: "alt__tag" }, "Suggested") : null),
        h("div", { class: "alt__meta" }, o.priced
          ? `${o.packs} \u00d7 ${o.size_text} \u00b7 uses ${o.used_pct ?? 0}%` + (o.left_over ? ` \u00b7 ${o.left_over} left` : "")
          : "We couldn't work out how much you need. Check the amount; 1 pack is added.")),
      h("div", { class: "alt__price" }, o.price.toFixed(2)),
      cur && o.offer.variant_id === cur
        ? h("span", { class: "alt__selected" }, icon("check", 14), "Selected")
        : h("button", { type: "button", class: "btn btn--small", onClick: () => {
            if (o.suggested) state.swapped.delete(r.key); else state.swapped.set(r.key, pickFromAlt(o));
            state.picker.delete(r.key); render();
          } }, "Choose"))));
}

function rowEl(r) {
  const open = r.status !== "priced";
  const cls = r.isHave ? "row row--have" : open ? "row row--open" : "row";
  const form = state.forms.get(r.key);
  const need = h("span", { class: r.estimated ? "est" : null, title: r.estimated ? "Estimated from the source" : null }, (r.estimated ? "~" : "") + r.need);

  let mid, amt;
  if (r.isHave) {
    mid = h("div", { class: "mid mid__have" }, icon("check", 18), "On your shelf. Not in the cart.");
    amt = r.status === "priced" ? h("div", { class: "amt amt--struck" }, r.price.toFixed(2)) : h("div", { class: "amt amt--none" }, "--");
  } else if (open) {
    const provider = state.cart.provider !== "catalogue";
    mid = h("div", { class: "mid mid__open" }, h("b", {}, provider ? "No match found yet" : "Not in your catalogue yet"),
      provider ? null : h("button", { type: "button", class: "btn btn--small", onClick: () => openForm(r) }, icon("plus", 14), "Price it"));
    amt = h("div", { class: "amt amt--none" }, "--");
  } else {
    const pct = r.used_pct;
    const picked = r.swappedFor && r.swappedFor.picked;
    mid = h("div", { class: "mid" },
      h("div", { class: "mid__product" }, r.product, " ", h("span", { class: "mid__packs" }, `×${r.packs}`)),
      pct == null ? null : h("div", { class: "gauge", role: "img", "aria-label": `Uses ${pct}% of the pack` }, h("div", { class: "gauge__fill", style: `width:${pct}%` })),
      pct == null ? null : h("div", { class: "mid__uses" }, `uses ${pct}%` + (r.left_over ? ` · ${r.left_over} left` : "")),
      r.swappedFor ? h("div", { class: "mid__swapped" },
        picked ? (r.unsure ? "Your choice. Check the amount: 1 pack added. " : "Your choice. ") : `Swapped in for ${r.name}. `,
        h("button", { type: "button", class: "btn btn--link", style: "min-height:auto;padding:0", onClick: () => { state.swapped.delete(r.key); render(); } }, "Undo")) : null,
      optionsFor(r).length > 1 ? h("button", { type: "button", class: "btn btn--link mid__change", "aria-expanded": String(state.picker.has(r.key)),
        onClick: () => { if (state.picker.has(r.key)) state.picker.delete(r.key); else state.picker.add(r.key); render(); } },
        state.picker.has(r.key) ? "Close" : "Change") : null);
    amt = h("div", { class: "amt" }, r.price.toFixed(2));
  }

  const swaps = open && !r.isHave && r.swaps && r.swaps.length ? h("div", { class: "swaps" },
    h("span", { class: "swaps__label" }, "Swap for"),
    r.swaps.map((s) => s.in_pantry
      ? h("button", { type: "button", class: "swap swap--have", title: s.note, onClick: () => setHave(r.name, true) }, icon("check", 14), `${s.product} (you have it)`)
      : h("button", { type: "button", class: "swap", title: s.note, onClick: () => { state.swapped.set(r.key, s); render(); } }, s.product + (s.packs > 1 ? ` ×${s.packs}` : ""), h("b", {}, s.price.toFixed(2))))) : null;

  const opts = !r.isHave ? optionsFor(r) : [];
  const showPicker = opts.length && (open ? true : state.picker.has(r.key));
  return h("li", { class: cls, "data-row": r.key },
    toggle(r), h("div", { class: "who" }, h("div", { class: "who__name" }, r.name), h("div", { class: "who__need" }, need)), mid, amt,
    open && !r.isHave && form ? formEl(r, form) : null, swaps, showPicker ? pickerEl(r, opts) : null);
}

// ---- add to catalogue, in place ----
function openForm(r) {
  const size = r.suggested_pack_size || 500;
  const unit = r.need_unit || "g";
  const label = unit === "count" ? "" : ` ${size >= 1000 && size % 1000 === 0 ? size / 1000 + (unit === "g" ? "kg" : "l") : size + unit}`;
  state.forms.set(r.key, { name: titleCase(r.name) + label, size: String(size), unit, price: "", error: "", saving: false, need: r.need });
  state.focus = `[data-row="${CSS.escape(r.key)}"] input[name=price]`;
  render();
}
const titleCase = (s) => s.replace(/\b\w/g, (c) => c.toUpperCase());

function formEl(r, f) {
  const bind = (prop) => (e) => { f[prop] = e.target.value; };
  const err = h("div", { class: "form__err", role: "alert" }, f.error);
  return h("form", { class: "form", novalidate: true, onSubmit: async (e) => {
      e.preventDefault();
      const body = { name: f.name.trim(), pack_size: parseFloat(f.size), unit: f.unit, price: parseFloat(f.price) };
      if (!body.name || !(body.pack_size > 0) || !(body.price >= 0)) { f.error = "Enter a name, a pack size above 0 and a price."; render(); return; }
      f.saving = true; f.error = ""; render();
      const res = await createProduct(body);
      if (!res.ok) { f.saving = false; f.error = res.error; state.focus = `[data-row="${CSS.escape(r.key)}"] input[name=price]`; render(); return; }
      state.forms.delete(r.key);
      toast(`Added ${body.name} to your catalogue`);
      await rebuild();
    } },
    h("div", { class: "form__title" }, `Add ${r.name} to your catalogue`),
    h("div", { class: "form__fields" },
      field("Product name", "field--name", h("input", { name: "name", value: f.name, onInput: bind("name"), autocomplete: "off" })),
      field(`Pack size`, "field--size", h("input", { name: "size", type: "number", min: "0", step: "any", inputmode: "decimal", value: f.size, onInput: bind("size") })),
      field("Unit", "field--unit", h("select", { name: "unit", onChange: bind("unit") }, ["g", "ml", "count"].map((u) => h("option", { value: u, selected: u === f.unit }, u)))),
      field("Price", "field--price", h("input", { name: "price", type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "0.00", value: f.price, onInput: bind("price") })),
      h("button", { type: "submit", class: "btn btn--primary", disabled: f.saving }, f.saving ? "Adding..." : "Add & update cart"),
      h("button", { type: "button", class: "btn btn--link", onClick: () => { state.forms.delete(r.key); render(); } }, "Cancel")),
    h("div", { class: "form__note" }, `The pack size is suggested from the ${f.need} this recipe needs. Change it to match the shelf.`), err);
}
function field(label, cls, control) {
  const id = "f_" + Math.random().toString(36).slice(2, 8);
  control.id = id;
  return h("div", { class: "field " + cls }, h("label", { for: id }, label), control);
}

function cartPanel(sum, anyEstimated, cart) {
  const source = cart.source;
  const instamart = cart.provider === "instamart";
  const parts = [`${sum.priced} priced`];
  if (sum.open) parts.push(`${sum.open} to price`);
  if (sum.haveN) parts.push(`${sum.haveN} you have`);
  const msg = sum.open
    ? `${sum.open} ingredient${sum.open === 1 ? " has" : "s have"} no price yet. Price them, swap them, or tap Have if they're in your kitchen.`
    : "Everything is priced. Copy your list and go shopping.";
  return h("aside", { class: "cart", "aria-label": "Your cart" },
    h("div", { class: "eyebrow" }, "Your cart"),
    h("div", { class: "cart__total" }, sum.total.toFixed(2)),
    h("div", { class: "cart__counts" }, parts.join(" · ")),
    h("div", { class: "seg", "aria-hidden": "true" }, sum.rows.map((r) => h("i", { class: r.isHave ? "" : r.status === "priced" ? "priced" : "open" }))),
    h("div", { class: "cart__msg" }, msg),
    instamart
      ? h("div", { class: "cart__actions" },
          h("button", { type: "button", class: "btn btn--primary cart__go", disabled: state.reviewBusy || !sum.priced, onClick: reviewOrder }, state.reviewBusy ? "Checking..." : "Review order"),
          h("button", { type: "button", class: "btn btn--link", onClick: copyList }, "Copy shopping list"))
      : h("button", { type: "button", class: "btn btn--ink", onClick: copyList }, "Copy shopping list"),
    instamart ? h("div", { class: "cart__badge" }, h("span", { class: "brandbar__logo", "aria-hidden": "true" }, "i"), cart.provider_badge || "Powered by Instamart") : null,
    h("div", { class: "cart__note" }, anyEstimated
      ? "Quantities marked in yellow came from the source and are estimates. Check them before you shop."
      : source === "dish" ? "Quantities are typical for this dish." : "Quantities were read from the source."));
}

// ---------- review and order (Instamart demo) ----------
async function reviewOrder() {
  const sum = summarize(state.cart.lines, state.have, state.swapped);
  const items = orderItems(sum);
  if (!items.length) { toast("Nothing to order yet."); return; }
  state.reviewBusy = true; render();
  const r = await postReview(items);
  state.reviewBusy = false;
  if (!r.ok) { toast(r.error); render(); return; }
  const excluded = sum.rows.filter((x) => !x.isHave && x.status !== "priced").map((x) => x.name);
  state.review = { data: r.data, items, excluded, method: (r.data.payment_methods[0] || {}).id || "Cash", error: "", placing: false };
  state.view = "review"; history.pushState({ view: "review" }, ""); render(); window.scrollTo(0, 0);
}
function postReview(items) {
  return request("/instamart/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) });
}

async function refreshReview() {
  const rv = state.review; rv.placing = true; rv.error = ""; render();
  const r = await postReview(rv.items);
  rv.placing = false;
  if (r.ok) { rv.data = r.data; rv.stale = false; if (!r.data.payment_methods.some((m) => m.id === rv.method)) rv.method = (r.data.payment_methods[0] || {}).id; }
  else rv.error = r.error;
  render();
}

async function placeOrder() {
  const rv = state.review; rv.placing = true; rv.error = ""; render();
  const r = await request("/instamart/checkout", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: rv.items, payment_method: rv.method, to_pay: rv.data.to_pay, confirmed: true, address_id: rv.data.address.id }) });
  rv.placing = false;
  if (!r.ok) { rv.error = r.error; rv.stale = r.status === 409; render(); return; }
  state.order = r.data; state.view = "done"; history.pushState({ view: "done" }, ""); render(); window.scrollTo(0, 0);
  if (r.data.status === "pending_payment") pollPayment();
}

let payTimer;
function pollPayment() {
  clearTimeout(payTimer);
  const o = state.order, started = Date.now();
  const tick = async () => {
    if (state.view !== "done" || state.order !== o) return;
    const r = await request("/instamart/payment-status", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paas_id: o.paas_id, order_id: o.order_ids[0] }) });
    if (state.order !== o) return;
    if (!r.ok) { o.error = r.error; render(); return; }
    if (r.data.status === "success") { state.order = Object.assign({}, o, { status: "placed", message: r.data.message }); render(); return; }
    if (r.data.status === "failed") { o.status = "failed"; o.message = r.data.message; render(); return; }
    if (Date.now() - started > (o.max_poll_ms || 20000)) { o.timedOut = true; render(); return; }
    payTimer = setTimeout(tick, o.poll_interval_ms || 1500);
  };
  payTimer = setTimeout(tick, o.poll_interval_ms || 1500);
}

const money = (v) => (v == null || v === "" ? "" : String(v));

function reviewView() {
  const rv = state.review, d = rv.data;
  const methodLabel = (d.payment_methods.find((m) => m.id === rv.method) || {}).label || rv.method;
  const warnings = [...(d.warnings || []),
    ...(d.removed_out_of_stock || []).map((n) => `${n} is out of stock and was removed.`),
    ...(d.reduced || []).map((x) => `${x.itemName}: only ${x.cappedQuantity} allowed per order (you asked for ${x.requestedQuantity}).`)];

  const items = h("div", { class: "rv__card" },
    h("h2", { class: "rv__h" }, "Your items"),
    h("ul", { class: "rv__items" }, d.items.map((i) => h("li", {},
      h("div", {}, h("div", { class: "rv__name" }, i.name), h("div", { class: "rv__variant" }, `${i.variant} \u00d7 ${i.quantity}`.replace("\\u00d7", "×"))),
      h("div", { class: "rv__price" }, i.unit_price == null ? "" : (i.unit_price * i.quantity).toFixed(2))))),
    h("dl", { class: "rv__bill" }, ...d.bill_lines.flatMap((b) => [h("dt", {}, b.label), h("dd", {}, money(b.value))])),
    h("div", { class: "rv__topay" }, h("span", {}, "To pay"), h("b", {}, money(d.to_pay))),
    rv.excluded.length ? h("p", { class: "rv__excluded" }, `Not in this order (no product chosen): ${rv.excluded.join(", ")}.`) : null,
    warnings.map((w) => h("div", { class: "alert" }, w)));

  const pay = h("fieldset", { class: "rv__pay" },
    h("legend", {}, "Payment"),
    d.payment_methods.map((m) => h("label", { class: "pay" + (rv.method === m.id ? " pay--on" : "") },
      h("input", { type: "radio", name: "pay", value: m.id, checked: rv.method === m.id, onChange: () => { rv.method = m.id; render(); } }), m.label)));

  const confirmBox = h("aside", { class: "cart rv__confirm", "aria-label": "Confirm your order" },
    h("div", { class: "eyebrow" }, "Delivery"),
    h("div", { class: "rv__addr" }, h("b", {}, d.address.name || "Delivery address"), h("div", {}, d.address.line)),
    pay,
    h("p", { class: "rv__say" }, `Your order will be delivered to: ${d.address.line}. You'll pay ${money(d.to_pay)} by ${methodLabel}.`),
    rv.error ? h("div", { class: "alert alert--error", role: "alert" }, rv.error) : null,
    rv.stale
      ? h("button", { type: "button", class: "btn btn--ink", disabled: rv.placing, onClick: refreshReview }, "Review the cart again")
      : h("button", { type: "button", class: "btn btn--primary cart__go", disabled: rv.placing, onClick: placeOrder }, rv.placing ? "Placing..." : `Place order \u00b7 ${money(d.to_pay)}`.replace("\\u00b7", "·")),
    h("button", { type: "button", class: "btn btn--link", onClick: () => { state.view = "recipe"; render(); window.scrollTo(0, 0); } }, "Back to ingredients"),
    h("div", { class: "cart__badge" }, h("span", { class: "brandbar__logo", "aria-hidden": "true" }, "i"), d.badge));

  return [
    h("nav", { class: "steps", "aria-label": "Progress" },
      h("span", { class: "done" }, "1 · Source ✓"), h("span", { class: "done" }, "2 · Ingredients ✓"), h("span", { class: "now" }, "3 · Review & order")),
    h("section", { class: "head" }, h("div", { class: "head__title" }, h("h1", { class: "title" }, "Review your order"))),
    h("div", { class: "alert demo-banner" }, h("strong", {}, "Demo mode. "), "This is a simulated Instamart: nothing here is real and no order will be placed."),
    h("div", { class: "grid" }, h("div", { class: "grid__main" }, brandBar(state.cart), items), confirmBox),
  ];
}

function doneView() {
  const o = state.order;
  const status = o.status;
  const body = status === "pending_payment" ? [
    h("h1", { class: "title" }, "Waiting for your payment"),
    h("p", { class: "done__lede" }, `Pay ${money(o.total)} with UPI.`),
    h("div", { class: "loading" }, h("div", { class: "loading__bar" }), h("div", { class: "loading__text" }, o.timedOut ? "This is taking longer than expected." : "Waiting for your payment...")),
    h("p", { class: "done__note" }, "In the live app this opens Swiggy's payment page, where you scan a QR code or open your UPI app. In this demo the payment completes by itself in a few seconds."),
    o.timedOut ? h("button", { type: "button", class: "btn btn--ink", onClick: () => { o.timedOut = false; render(); pollPayment(); } }, "Check again") : null,
    o.error ? h("div", { class: "alert alert--error", role: "alert" }, o.error) : null,
  ] : status === "failed" ? [
    h("h1", { class: "title" }, "Payment didn't go through"),
    h("div", { class: "alert alert--error", role: "alert" }, o.message || "No order was placed."),
    h("button", { type: "button", class: "btn btn--ink", onClick: () => { state.view = "review"; render(); } }, "Back to your order"),
  ] : [
    h("div", { class: "done__tick", "aria-hidden": "true" }, icon("check", 36)),
    h("h1", { class: "title" }, o.message || "Instamart order placed successfully."),
    h("dl", { class: "done__facts" },
      h("dt", {}, "Order"), h("dd", {}, (o.order_ids || []).join(", ")),
      h("dt", {}, "Delivering to"), h("dd", {}, o.delivery_address || state.review.data.address.line),
      h("dt", {}, "Paid by"), h("dd", {}, o.payment_method === "UPI" ? "UPI" : "Cash on delivery"),
      h("dt", {}, "Total"), h("dd", {}, money(o.total))),
    h("p", { class: "done__note" }, "Demo: no real order was placed. To cancel a real Instamart order, the customer calls Swiggy support."),
    h("button", { type: "button", class: "btn btn--ink", onClick: backToStart }, "Plan another recipe"),
  ];
  return [
    h("section", { class: "ordered" }, h("div", { class: "demo-pill" }, "Demo: simulated data"), body, h("div", { class: "cart__badge" }, h("span", { class: "brandbar__logo", "aria-hidden": "true" }, "i"), o.badge || "Powered by Instamart")),
  ];
}

// ---------- render ----------
function render() {
  const y = window.scrollY;
  const same = app.dataset.view === state.view;
  const views = { start: startView, recipe: recipeView, review: reviewView, done: doneView };
  app.replaceChildren(...(views[state.view] || startView)());
  app.dataset.view = state.view;
  if (same) window.scrollTo(0, y);
  if (state.focus) { const el = document.querySelector(state.focus); if (el) el.focus(); state.focus = null; }
}

window.addEventListener("popstate", () => {
  if (state.view === "done") { clearTimeout(payTimer); state.view = "start"; }
  else if (state.view === "review") state.view = "recipe";
  else if (state.view === "recipe") state.view = "start";
  else return;
  render();
});
window.RC = { state, summarize, listText, orderItems };   // handy for debugging in the console

(async () => {
  const r = await request("/api/config");
  if (r.ok) state.config = r.data;
  if (state.config.demo_instamart) { try { if (localStorage.getItem("rc.provider") === "instamart") state.provider = "instamart"; } catch { /* ignore */ } }
  render();
})();
