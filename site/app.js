// Footnote's page: ask, then show the answer with its citations linked to the sources.
// Everything from the API is inserted as text, never as HTML.

const API = (window.FOOTNOTE_API || "http://localhost:8000/").replace(/\/?$/, "/");
const EXAMPLES = [
  "What was Apple's revenue in fiscal 2024?",
  "Where does Apple make its products?",
  "How does JPMorgan manage cybersecurity risk?",
  "Compare Exxon's and Chevron's net income in fiscal 2024",
  "What are Tesla's main supply chain risks?",
  "How did Walmart's net income change over the last two years?",
];

const $ = (id) => document.getElementById(id);
const form = $("ask"), input = $("q"), go = $("go"), status = $("status");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

for (const example of EXAMPLES) {
  $("examples").append(el("li", {}, el("button", { type: "button", onclick: () => { input.value = example; ask(example); } }, example)));
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(input.value.trim());
});

// What's covered right now, written each morning by the site workflow.
fetch("status.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((s) => {
    if (!s) return;
    const when = new Date(s.generated_at).toLocaleDateString([], { month: "short", day: "numeric" });
    $("coverage").textContent =
      `${s.filings} annual reports · ${s.companies} companies · fiscal ${s.fiscal_years[0]}–${s.fiscal_years[1]} · ` +
      `${s.passages.toLocaleString()} passages · updated ${when}`;
  })
  .catch(() => {});

const fromUrl = new URLSearchParams(location.search).get("q");
if (fromUrl) { input.value = fromUrl; ask(fromUrl); }

async function ask(question) {
  if (question.length < 3) return;
  history.replaceState(null, "", "?q=" + encodeURIComponent(question));
  go.disabled = true;
  status.className = "";
  status.textContent = "Reading the filings… usually under 10 seconds, longer if the server has been asleep.";
  $("result").classList.add("hidden");
  try {
    const response = await fetch(API + "ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.");
    show(body);
    status.textContent = body.cached ? "Answered earlier today; shown from the cache." : `Answered in ${body.seconds}s.`;
  } catch (error) {
    status.className = "error";
    status.textContent = error.message === "Failed to fetch" ? "Couldn't reach Footnote's API. Please try again in a moment." : error.message;
  } finally {
    go.disabled = false;
  }
}

function show(body) {
  const byId = Object.fromEntries(body.sources.map((s) => [s.id, s]));
  const answer = $("answer");
  answer.replaceChildren();
  for (const part of body.answer.split(/(\[[SF]\d+\])/)) {
    const match = part.match(/^\[([SF]\d+)\]$/);
    if (match && byId[match[1]]) {
      const id = match[1];
      answer.append(el("a", { class: "mark" + (id.startsWith("F") ? " fig" : ""), href: "#src-" + id, onclick: (e) => focusSource(e, id), title: label(byId[id]) }, id));
    } else {
      answer.append(part);
    }
  }

  const plan = body.plan || {};
  const bits = [];
  if (plan.tickers?.length) bits.push(plan.tickers.join(", "));
  if (plan.fiscal_years?.length) bits.push("FY" + plan.fiscal_years.join(", FY"));
  if (plan.metrics?.length) bits.push("figures: " + plan.metrics.join(", ").replaceAll("_", " "));
  if (plan.search) bits.push(`searched: "${plan.search}"`);
  $("plan").textContent = bits.length ? "Looked at " + bits.join(" · ") : "";

  const list = $("sources");
  list.replaceChildren(...body.sources.map(sourceItem));
  if (!body.sources.length) {
    const why = plan.in_scope === false ? "the question is outside what these filings cover." : "nothing in the filings matched.";
    list.append(el("li", {}, el("span"), el("p", { class: "small" }, "No sources: " + why)));
  }
  $("result").classList.remove("hidden");
}

function label(source) {
  return source.kind === "figure"
    ? `${source.company} FY${source.fiscal_year} · ${source.label}`
    : `${source.company} FY${source.fiscal_year} · Item ${source.item} ${source.item_title}`;
}

function sourceItem(source) {
  const isFigure = source.kind === "figure";
  const link = el("a", { href: source.url, target: "_blank", rel: "noopener" }, "the filing on SEC.gov");
  if (isFigure) {
    return el("li", { id: "src-" + source.id },
      el("span", { class: "sid fig" }, source.id),
      el("div", {},
        el("p", { class: "where" }, `${source.company} · fiscal ${source.fiscal_year} · ${source.label}`),
        el("p", { class: "value" }, source.text),
        el("p", { class: "small" }, `XBRL us-gaap:${source.concept}, as reported in `, link, ".")));
  }
  const excerpt = el("p", { class: "excerpt clamped" }, source.text);
  const more = el("button", { type: "button", onclick: () => { excerpt.classList.toggle("clamped"); more.textContent = excerpt.classList.contains("clamped") ? "Show the whole passage" : "Show less"; } }, "Show the whole passage");
  return el("li", { id: "src-" + source.id },
    el("span", { class: "sid" }, source.id),
    el("div", {},
      el("p", { class: "where" }, `${source.company} · fiscal ${source.fiscal_year} · Item ${source.item} ${source.item_title}`,
        source.heading ? el("span", {}, ` › ${source.heading}`) : null),
      excerpt,
      el("p", { class: "small" }, more, " · ", link)));
}

function focusSource(event, id) {
  event.preventDefault();
  const target = $("src-" + id);
  target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  target.classList.remove("flash");
  void target.offsetWidth;
  target.classList.add("flash");
}
