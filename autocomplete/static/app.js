const form = document.querySelector("#search-form");
const queryInput = document.querySelector("#query");
const searchButton = document.querySelector("#search-button");
const clearButton = document.querySelector("#clear-button");
const resultsList = document.querySelector("#results");
const emptyState = document.querySelector("#empty-state");
const searchMeta = document.querySelector("#search-meta");
const message = document.querySelector("#message");
const indexStatus = document.querySelector("#index-status");
let activeRequest = null;

function setLoading(value) {
  searchButton.disabled = value;
  searchButton.classList.toggle("loading", value);
  searchButton.setAttribute("aria-busy", String(value));
}
function showMessage(text) { message.textContent = text; message.hidden = false; }
function hideMessage() { message.textContent = ""; message.hidden = true; }
function clearResults() { resultsList.replaceChildren(); }
function locationLabel(count) {
  return count === 1 ? "1 corpus location" : `${count.toLocaleString()} corpus locations`;
}

async function loadLocations(suggestion, list, button) {
  const nextOffset = Number(button.dataset.nextOffset || 0);
  button.disabled = true;
  button.textContent = nextOffset ? "Loading more…" : "Loading locations…";
  try {
    const response = await fetch(
      `/api/completions/${suggestion.sentence_id}/locations?offset=${nextOffset}&limit=10`,
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Locations could not be loaded.");
    data.locations.forEach((location) => {
      const row = document.createElement("li");
      const file = document.createElement("span");
      file.textContent = location.source_text;
      const offset = document.createElement("span");
      offset.className = "location-offset";
      offset.textContent = `Line ${location.offset.toLocaleString()}`;
      row.append(file, offset);
      list.append(row);
    });
    if (data.next_offset === null) {
      button.remove();
    } else {
      button.dataset.nextOffset = String(data.next_offset);
      button.disabled = false;
      button.textContent = "Load more locations";
    }
  } catch (error) {
    button.disabled = false;
    button.textContent = "Try loading locations again";
    showMessage(error.message);
  }
}

function createResultCard(suggestion, position) {
  const item = document.createElement("li");
  item.className = "result-card";
  const top = document.createElement("div");
  top.className = "result-main";
  const number = document.createElement("span");
  number.className = "result-number";
  number.textContent = String(position);
  const content = document.createElement("div");
  content.className = "result-content";
  const sentence = document.createElement("p");
  sentence.className = "result-sentence";
  sentence.textContent = suggestion.completed_sentence;
  const badges = document.createElement("div");
  badges.className = "result-badges";
  const locationsBadge = document.createElement("span");
  locationsBadge.className = "location-badge";
  locationsBadge.textContent = locationLabel(suggestion.occurrence_count);
  const score = document.createElement("span");
  score.className = "score-badge";
  score.textContent = `Score ${suggestion.score}`;
  badges.append(locationsBadge, score);
  content.append(sentence, badges);
  top.append(number, content);
  item.append(top);

  const disclosure = document.createElement("button");
  disclosure.className = "locations-toggle";
  disclosure.type = "button";
  disclosure.setAttribute("aria-expanded", "false");
  const closedLabel = suggestion.occurrence_count === 1 ? "Show source" : "Show all locations";
  disclosure.textContent = closedLabel;
  const panel = document.createElement("div");
  panel.className = "locations-panel";
  panel.hidden = true;
  const list = document.createElement("ul");
  list.className = "locations-list";
  const loadMore = document.createElement("button");
  loadMore.type = "button";
  loadMore.className = "load-more-button";
  loadMore.textContent = "Load locations";
  loadMore.dataset.nextOffset = "0";
  loadMore.addEventListener("click", () => loadLocations(suggestion, list, loadMore));
  panel.append(list, loadMore);
  disclosure.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    disclosure.setAttribute("aria-expanded", String(opening));
    disclosure.textContent = opening ? "Hide locations" : closedLabel;
    if (opening && list.children.length === 0 && panel.contains(loadMore)) loadMore.click();
  });
  item.append(disclosure, panel);
  return item;
}

function renderResults(data) {
  clearResults();
  data.suggestions.forEach((suggestion, index) => resultsList.append(createResultCard(suggestion, index + 1)));
  const count = data.suggestions.length;
  emptyState.hidden = count > 0;
  if (count === 0) {
    emptyState.querySelector("h3").textContent = "No matches found";
    emptyState.querySelector("p").textContent = "Try a different phrase or check the spelling.";
  }
  const label = count === 1 ? "unique suggestion" : "unique suggestions";
  searchMeta.textContent = `${count} ${label} · ${data.elapsed_ms.toFixed(1)} ms`;
}

async function search(query) {
  if (activeRequest) activeRequest.abort();
  const request = new AbortController();
  activeRequest = request;
  setLoading(true);
  hideMessage();
  searchMeta.textContent = "Searching the index…";
  try {
    const response = await fetch("/api/completions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }), signal: request.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The search could not be completed.");
    renderResults(data);
  } catch (error) {
    if (error.name !== "AbortError") {
      clearResults();
      emptyState.hidden = false;
      emptyState.querySelector("h3").textContent = "Search unavailable";
      emptyState.querySelector("p").textContent = "Check the message above and try again.";
      searchMeta.textContent = "Search unavailable";
      showMessage(error.message);
    }
  } finally {
    if (activeRequest === request) { activeRequest = null; setLoading(false); }
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = queryInput.value;
  if (!query.trim()) { showMessage("Enter text containing searchable characters."); queryInput.focus(); return; }
  search(query);
});
queryInput.addEventListener("input", () => { clearButton.hidden = queryInput.value.length === 0; });
clearButton.addEventListener("click", () => {
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  queryInput.value = "";
  clearButton.hidden = true;
  clearResults();
  hideMessage();
  emptyState.hidden = false;
  emptyState.querySelector("h3").textContent = "Search the indexed corpus";
  emptyState.querySelector("p").textContent = "Your five best unique completions will appear here.";
  searchMeta.textContent = "Ready to search";
  setLoading(false);
  queryInput.focus();
});

fetch("/api/health").then((response) => response.json()).then((data) => {
  indexStatus.classList.toggle("offline", !data.index_ready);
  indexStatus.querySelector("span:last-child").textContent = data.index_ready ? "Index ready" : "Index missing";
}).catch(() => {
  indexStatus.classList.add("offline");
  indexStatus.querySelector("span:last-child").textContent = "Service unavailable";
});
