const form = document.querySelector("#search-form");
const queryInput = document.querySelector("#query");
const clearButton = document.querySelector("#clear-button");
const inputSpinner = document.querySelector("#input-spinner");
const suggestionsPopover = document.querySelector("#suggestions-popover");
const suggestionStatus = document.querySelector("#suggestion-status");
const liveSuggestions = document.querySelector("#live-suggestions");
const resultsSection = document.querySelector("#results-section");
const resultsList = document.querySelector("#results");
const searchMeta = document.querySelector("#search-meta");
const message = document.querySelector("#message");

let activeRequest = null;
let debounceTimer = null;
let latestData = null;
let activeSuggestionIndex = -1;
let suggestionSocket = null;
let socketQuery = "";
let pendingSocketQuery = null;
let reconnectTimer = null;
let reconnectDelay = 1500;
let pageIsUnloading = false;

function setLoading(value) {
  inputSpinner.hidden = !value;
  queryInput.setAttribute("aria-busy", String(value));
}

function showMessage(text) {
  message.textContent = text;
  message.hidden = false;
}

function hideMessage() {
  message.textContent = "";
  message.hidden = true;
}

function locationLabel(count) {
  return count === 1 ? "1 location" : `${count.toLocaleString()} locations`;
}

function openSuggestions() {
  suggestionsPopover.hidden = false;
  queryInput.setAttribute("aria-expanded", "true");
}

function closeSuggestions() {
  suggestionsPopover.hidden = true;
  queryInput.setAttribute("aria-expanded", "false");
  queryInput.removeAttribute("aria-activedescendant");
  activeSuggestionIndex = -1;
}

function setActiveSuggestion(index) {
  const options = [...liveSuggestions.querySelectorAll('[role="option"]')];
  if (options.length === 0) return;
  activeSuggestionIndex = (index + options.length) % options.length;
  options.forEach((option, optionIndex) => {
    const selected = optionIndex === activeSuggestionIndex;
    option.setAttribute("aria-selected", String(selected));
    option.classList.toggle("active", selected);
  });
  const activeOption = options[activeSuggestionIndex];
  queryInput.setAttribute("aria-activedescendant", activeOption.id);
  activeOption.scrollIntoView({ block: "nearest" });
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

function createResultCard(suggestion) {
  const item = document.createElement("li");
  item.className = "result-card";
  const top = document.createElement("div");
  top.className = "result-main selected-result-main";
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
  top.append(content);
  item.append(top);

  const disclosure = document.createElement("button");
  disclosure.className = "locations-toggle selected-locations-toggle";
  disclosure.type = "button";
  disclosure.setAttribute("aria-expanded", "false");
  const closedLabel = suggestion.occurrence_count === 1 ? "Show source" : "Show all locations";
  disclosure.textContent = closedLabel;
  const panel = document.createElement("div");
  panel.className = "locations-panel selected-locations-panel";
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

function recordSelectedCompletion(query, suggestion, rank, elapsedMs) {
  fetch("/api/completions/selection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      sentence_id: suggestion.sentence_id,
      rank,
      elapsed_ms: elapsedMs,
    }),
    keepalive: true,
  }).catch(() => {
    // Analytics must never interrupt the autocomplete interaction.
  });
}

function selectSuggestion(index) {
  if (!latestData || !latestData.suggestions[index]) return;
  const selectedData = latestData;
  const suggestion = selectedData.suggestions[index];
  recordSelectedCompletion(
    selectedData.query,
    suggestion,
    index + 1,
    selectedData.elapsed_ms,
  );
  queryInput.value = suggestion.completed_sentence;
  clearButton.hidden = false;
  closeSuggestions();
  hideMessage();
  resultsList.replaceChildren(createResultCard(suggestion));
  searchMeta.textContent = `${locationLabel(suggestion.occurrence_count)} · ${selectedData.elapsed_ms.toFixed(1)} ms`;
  resultsSection.hidden = false;
  latestData = null;
}

function renderSuggestions(data) {
  latestData = data;
  liveSuggestions.replaceChildren();
  activeSuggestionIndex = -1;

  if (data.suggestions.length === 0) {
    suggestionStatus.hidden = false;
    suggestionStatus.textContent = "No matching sentences found.";
    openSuggestions();
    return;
  }

  suggestionStatus.hidden = true;
  data.suggestions.forEach((suggestion, index) => {
    const option = document.createElement("li");
    option.id = `suggestion-${index}`;
    option.className = "live-suggestion";
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", "false");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "live-suggestion-button";
    const icon = document.createElement("span");
    icon.className = "suggestion-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "⌕";
    const sentence = document.createElement("span");
    sentence.className = "suggestion-sentence";
    sentence.textContent = suggestion.completed_sentence;
    const count = document.createElement("span");
    count.className = "suggestion-count";
    count.textContent = locationLabel(suggestion.occurrence_count);
    button.append(icon, sentence, count);
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => selectSuggestion(index));
    option.append(button);
    liveSuggestions.append(option);
  });
  openSuggestions();
}

function createEditMessage(previousQuery, nextQuery) {
  let keep = 0;
  const sharedLength = Math.min(previousQuery.length, nextQuery.length);
  while (
    keep < sharedLength
    && previousQuery[keep] === nextQuery[keep]
  ) {
    keep += 1;
  }

  let sharedSuffix = 0;
  while (
    sharedSuffix < previousQuery.length - keep
    && sharedSuffix < nextQuery.length - keep
    && previousQuery[previousQuery.length - sharedSuffix - 1]
      === nextQuery[nextQuery.length - sharedSuffix - 1]
  ) {
    sharedSuffix += 1;
  }

  return {
    type: "edit",
    keep,
    delete: previousQuery.length - keep - sharedSuffix,
    insert: nextQuery.slice(keep, nextQuery.length - sharedSuffix),
  };
}

function sendQueryOverWebSocket(query) {
  if (!suggestionSocket || suggestionSocket.readyState !== WebSocket.OPEN) {
    return false;
  }

  const setMessage = { type: "set", query };
  const editMessage = createEditMessage(socketQuery, query);
  const serializedSet = JSON.stringify(setMessage);
  const serializedEdit = JSON.stringify(editMessage);
  const serializedMessage = serializedEdit.length < serializedSet.length
    ? serializedEdit
    : serializedSet;

  try {
    suggestionSocket.send(serializedMessage);
    socketQuery = query;
    pendingSocketQuery = query;
    return true;
  } catch (_error) {
    suggestionSocket.close();
    return false;
  }
}

function connectSuggestionSocket() {
  clearTimeout(reconnectTimer);
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/completions`);
  suggestionSocket = socket;

  socket.addEventListener("open", () => {
    socketQuery = "";
    reconnectDelay = 1500;
  });

  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (_error) {
      return;
    }

    if (data.type === "error") {
      if (typeof data.query === "string") socketQuery = data.query;
      pendingSocketQuery = null;
      if (data.query !== queryInput.value && queryInput.value.trim()) {
        requestSuggestions(queryInput.value);
        return;
      }
      setLoading(false);
      closeSuggestions();
      showMessage(data.detail || "The search could not be completed.");
      return;
    }

    if (data.query !== queryInput.value) return;
    pendingSocketQuery = null;
    setLoading(false);

    if (data.type === "suggestions") {
      renderSuggestions(data);
    }
  });

  socket.addEventListener("close", () => {
    if (suggestionSocket === socket) suggestionSocket = null;
    socketQuery = "";
    const interruptedQuery = pendingSocketQuery;
    pendingSocketQuery = null;

    if (interruptedQuery && interruptedQuery === queryInput.value) {
      requestSuggestions(interruptedQuery);
    }
    if (!pageIsUnloading) {
      reconnectTimer = setTimeout(connectSuggestionSocket, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    }
  });
}

async function requestSuggestions(query) {
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  setLoading(true);
  hideMessage();
  suggestionStatus.hidden = false;
  suggestionStatus.textContent = "Searching…";
  liveSuggestions.replaceChildren();
  openSuggestions();

  if (sendQueryOverWebSocket(query)) return;

  const request = new AbortController();
  activeRequest = request;

  try {
    const response = await fetch("/api/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal: request.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The search could not be completed.");
    if (queryInput.value === query) renderSuggestions(data);
  } catch (error) {
    if (error.name !== "AbortError" && queryInput.value === query) {
      closeSuggestions();
      showMessage(error.message);
    }
  } finally {
    if (activeRequest === request) {
      activeRequest = null;
      setLoading(false);
    }
  }
}

function queueSuggestions() {
  const query = queryInput.value;
  latestData = null;
  clearButton.hidden = query.length === 0;
  resultsSection.hidden = true;
  hideMessage();
  clearTimeout(debounceTimer);
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  setLoading(false);

  if (!query.trim()) {
    latestData = null;
    closeSuggestions();
    return;
  }

  suggestionStatus.hidden = false;
  suggestionStatus.textContent = "Waiting for input…";
  liveSuggestions.replaceChildren();
  openSuggestions();
  debounceTimer = setTimeout(() => requestSuggestions(query), 150);
}

function showSuggestionsForCurrentInput() {
  const query = queryInput.value;
  if (!query.trim()) return;
  if (latestData?.query === query) {
    openSuggestions();
  } else {
    queueSuggestions();
  }
}

queryInput.addEventListener("input", queueSuggestions);
queryInput.addEventListener("focus", showSuggestionsForCurrentInput);
queryInput.addEventListener("click", showSuggestionsForCurrentInput);
queryInput.addEventListener("keydown", (event) => {
  const suggestionCount = latestData?.suggestions.length || 0;
  if (event.key === "ArrowDown" && suggestionCount) {
    event.preventDefault();
    openSuggestions();
    setActiveSuggestion(activeSuggestionIndex + 1);
  } else if (event.key === "ArrowUp" && suggestionCount) {
    event.preventDefault();
    openSuggestions();
    setActiveSuggestion(activeSuggestionIndex - 1);
  } else if (event.key === "Escape") {
    closeSuggestions();
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!latestData?.suggestions.length) return;
  selectSuggestion(activeSuggestionIndex >= 0 ? activeSuggestionIndex : 0);
});

clearButton.addEventListener("click", () => {
  clearTimeout(debounceTimer);
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  latestData = null;
  queryInput.value = "";
  clearButton.hidden = true;
  setLoading(false);
  closeSuggestions();
  hideMessage();
  resultsSection.hidden = true;
  resultsList.replaceChildren();
  queryInput.focus();
});

document.addEventListener("pointerdown", (event) => {
  if (!form.contains(event.target)) closeSuggestions();
});

window.addEventListener("beforeunload", () => {
  pageIsUnloading = true;
  clearTimeout(reconnectTimer);
});

connectSuggestionSocket();
