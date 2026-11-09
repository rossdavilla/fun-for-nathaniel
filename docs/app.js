"use strict";

const INTERESTS = {
  animals: "Animals & farms",
  science: "Science & vehicles",
  arts: "Arts, music & stories",
  festival: "Festivals & play",
};

const state = {
  events: [],
  freeDays: [],
  year: 0,
  month: 0,
  day: null,
  tags: new Set(),
  city: "",
  showAll: false,
};

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

const pad = (n) => String(n).padStart(2, "0");
const ymd = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
const dayOf = (event) => event.start.slice(0, 10);
const lastDayOf = (event) => (event.end || event.start).slice(0, 10);

function todayString() {
  const now = new Date();
  return ymd(now.getFullYear(), now.getMonth(), now.getDate());
}

function freeDayName(day) {
  const period = state.freeDays.find((p) => p.start <= day && day <= p.end);
  return period ? period.name : null;
}

function isFree(day) {
  const [y, m, d] = day.split("-").map(Number);
  const weekday = new Date(y, m - 1, d).getDay();
  return weekday === 0 || weekday === 6 || Boolean(freeDayName(day));
}

function isWebLink(url) {
  return /^https?:\/\//i.test(url || "");
}

function matchesFilters(event) {
  if (state.city && event.city !== state.city) return false;
  if (state.tags.size && !event.tags.some((tag) => state.tags.has(tag))) return false;
  return true;
}

const visibleEvents = () =>
  state.events.filter((event) => matchesFilters(event) && (state.showAll || event.available));

const inShownMonth = (event) =>
  dayOf(event).startsWith(`${state.year}-${pad(state.month + 1)}-`);

function monthLimits() {
  const today = todayString();
  const lastEvent = state.events.length ? dayOf(state.events[state.events.length - 1]) : today;
  const toIndex = (s) => Number(s.slice(0, 4)) * 12 + Number(s.slice(5, 7)) - 1;
  return { first: toIndex(today), last: Math.max(toIndex(today), toIndex(lastEvent)) };
}

function renderCalendar() {
  const title = new Date(state.year, state.month, 1).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });
  $("month-title").textContent = title;

  const { first, last } = monthLimits();
  const current = state.year * 12 + state.month;
  $("prev").disabled = current <= first;
  $("next").disabled = current >= last;

  const today = todayString();
  const byDay = new Map();
  for (const event of visibleEvents()) {
    const key = dayOf(event);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(event);
  }

  const grid = $("grid");
  grid.replaceChildren();
  const leading = new Date(state.year, state.month, 1).getDay();
  const count = new Date(state.year, state.month + 1, 0).getDate();
  for (let i = 0; i < leading; i += 1) {
    grid.append(el("span", { class: "day empty", "aria-hidden": "true" }));
  }
  for (let d = 1; d <= count; d += 1) {
    const key = ymd(state.year, state.month, d);
    const events = byDay.get(key) || [];
    const classes = ["day"];
    if (isFree(key)) classes.push("free");
    if (key < today) classes.push("past");
    if (key === today) classes.push("today");
    if (events.length) classes.push("has-events");
    if (key === state.day) classes.push("selected");

    const dots = el("span", { class: "dots", "aria-hidden": "true" });
    for (const event of events.slice(0, 3)) {
      dots.append(el("span", { class: `dot ${event.tags[0] || ""}` }));
    }
    const label = `${title.split(" ")[0]} ${d}, ${events.length} event${events.length === 1 ? "" : "s"}`;
    const cell = el("button", { class: classes.join(" "), "aria-label": label }, String(d), dots);
    if (key === state.day) cell.setAttribute("aria-pressed", "true");
    if (events.length) {
      cell.addEventListener("click", () => {
        state.day = state.day === key ? null : key;
        render();
      });
    } else {
      cell.disabled = true;
    }
    grid.append(cell);
  }
}

function renderCard(event) {
  const color = event.tags[0] ? `var(--${event.tags[0]})` : "var(--line)";
  const where = event.location ? ` · ${event.location}` : "";
  const card = el("article", { class: "card", style: `--card-color: ${color}` },
    el("h4", {}, event.title),
    el("p", { class: "meta" }, `${event.time_label}${where}`));

  if (event.summary) card.append(el("p", { class: "summary" }, event.summary));
  if (event.check_time) {
    card.append(el("p", { class: "warning" },
      `The listing says ${event.time_label}, which looks wrong. Check with the organizer.`));
  }

  const labels = el("div", { class: "labels" });
  if (event.fit === "great") labels.append(el("span", { class: "label best" }, "Made for kids"));
  for (const tag of event.tags) {
    labels.append(el("span", { class: `label ${tag}` }, INTERESTS[tag]));
  }
  if (event.cost) labels.append(el("span", { class: "label plain" }, event.cost));
  if (!event.available) labels.append(el("span", { class: "label plain" }, "School week"));
  card.append(labels);

  const actions = el("div", { class: "actions" });
  if (isWebLink(event.google_url)) {
    actions.append(el("a", {
      class: "button", href: event.google_url, target: "_blank", rel: "noopener",
      "aria-label": `Add ${event.title} to Google Calendar`,
    }, "Add to Google Calendar"));
  }
  if (isWebLink(event.url)) {
    actions.append(el("a", {
      class: "button ghost", href: event.url, target: "_blank", rel: "noopener",
      "aria-label": `Details for ${event.title} on ${event.source}`,
    }, "Details"));
  }
  card.append(actions);
  return card;
}

function longDate(day, options = { weekday: "long", month: "long", day: "numeric" }) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", options);
}

function renderAgenda() {
  const today = todayString();
  const upcoming = (event) => lastDayOf(event) >= today;
  const list = visibleEvents().filter((event) =>
    upcoming(event) && (state.day ? dayOf(event) === state.day : inShownMonth(event)));

  $("agenda-title").textContent = state.day
    ? longDate(state.day)
    : `Coming up in ${new Date(state.year, state.month, 1).toLocaleDateString("en-US", { month: "long" })}`;
  $("clear-day").hidden = !state.day;

  const agenda = $("agenda");
  agenda.replaceChildren();

  if (!list.length) {
    agenda.append(el("p", { class: "empty-state" },
      "Nothing found for this selection yet. Try another month or clear a filter."));
  }

  const groups = new Map();
  for (const event of list) {
    const key = dayOf(event);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  }
  for (const [day, events] of groups) {
    const heading = el("h3", {}, longDate(day));
    const breakName = freeDayName(day);
    if (breakName) heading.append(el("span", { class: "break-badge" }, breakName));
    const group = el("div", { class: "day-group" }, heading);
    for (const event of events) group.append(renderCard(event));
    agenda.append(group);
  }

  if (!state.showAll && !state.day) {
    const hidden = state.events.filter((event) =>
      !event.available && matchesFilters(event) && upcoming(event) && inShownMonth(event)).length;
    if (hidden) {
      agenda.append(el("p", { class: "more" }, el("button", {
        class: "link-button",
        onclick: () => {
          state.showAll = true;
          $("show-all").checked = true;
          render();
        },
      }, `Show ${hidden} more on school days`)));
    }
  }
}

function render() {
  renderCalendar();
  renderAgenda();
}

function shiftMonth(delta) {
  const date = new Date(state.year, state.month + delta, 1);
  state.year = date.getFullYear();
  state.month = date.getMonth();
  state.day = null;
  render();
}

function buildFilters() {
  const chips = $("interests");
  for (const [tag, label] of Object.entries(INTERESTS)) {
    const chip = el("button", {
      class: "chip", "aria-pressed": "false", style: `--chip: var(--${tag})`,
    }, label);
    chip.addEventListener("click", () => {
      if (state.tags.has(tag)) state.tags.delete(tag);
      else state.tags.add(tag);
      chip.setAttribute("aria-pressed", String(state.tags.has(tag)));
      state.day = null;
      render();
    });
    chips.append(chip);
  }

  const towns = [...new Set(state.events.map((event) => event.city))].sort();
  const select = $("city");
  select.append(el("option", { value: "" }, "All nearby towns"));
  for (const town of towns) select.append(el("option", { value: town }, town));
  select.addEventListener("change", () => {
    state.city = select.value;
    state.day = null;
    render();
  });

  $("show-all").addEventListener("change", (change) => {
    state.showAll = change.target.checked;
    state.day = null;
    render();
  });
}

function buildFooter(data) {
  const feedPath = new URL("nathaniel-picks.ics", location.href);
  const webcal = `webcal://${feedPath.host}${feedPath.pathname}`;
  $("subscribe").href = `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(webcal)}`;
  const copy = $("copy-feed");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(feedPath.href);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = feedPath.href;
    }
  });

  const updated = new Date(data.generated_at).toLocaleDateString("en-US", {
    month: "long", day: "numeric", year: "numeric",
  });
  $("updated").textContent = `Last checked ${updated} · ${data.sources.length} sources`;
  const list = $("source-list");
  for (const source of data.sources) {
    const note = source.state === "failed" ? " (couldn't be reached, using last copy)" : "";
    list.append(el("li", {}, `${source.name}${note}`));
  }
}

async function start() {
  try {
    const response = await fetch("data/events.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    state.events = data.events;
    state.freeDays = data.free_days;
    const now = new Date();
    state.year = now.getFullYear();
    state.month = now.getMonth();
    $("prev").addEventListener("click", () => shiftMonth(-1));
    $("next").addEventListener("click", () => shiftMonth(1));
    $("clear-day").addEventListener("click", () => {
      state.day = null;
      render();
    });
    buildFilters();
    buildFooter(data);
    render();
  } catch (error) {
    console.error(error);
    $("agenda").replaceChildren(el("p", { class: "notice" },
      "The events couldn't be loaded. Please try again in a little while."));
  }
}

start();
