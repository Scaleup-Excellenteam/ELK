(() => {
  const indexStatus = document.getElementById("index-status");
  const autoRefreshBox = document.getElementById("auto-refresh");
  const logBody = document.getElementById("log-body");
  const logEmpty = document.getElementById("log-empty");

  const statElements = {
    sentence_count: document.getElementById("stat-sentences"),
    source_count: document.getElementById("stat-sources"),
    location_count: document.getElementById("stat-locations"),
  };
  const p95Element = document.getElementById("stat-p95");
  const averageElement = document.getElementById("stat-average");
  const cacheRateElement = document.getElementById("stat-cache-rate");
  const cacheContextElement = document.getElementById("stat-cache-context");
  const selectionsElement = document.getElementById("stat-selections");
  const charactersElement = document.getElementById("stat-characters");
  const cacheImpactValue = document.getElementById("cache-impact-value");
  const cacheImpactCopy = document.getElementById("cache-impact-copy");
  const cacheProgress = document.getElementById("cache-progress");
  const cacheProgressFill = document.getElementById("cache-progress-fill");
  const slowSearchesElement = document.getElementById("slow-searches");
  const errorCountElement = document.getElementById("error-count");
  const latencyChart = document.getElementById("latency-chart");
  const latencyEmpty = document.getElementById("latency-empty");
  const latencyLabels = document.getElementById("latency-labels");
  const latencyArea = document.getElementById("latency-area");
  const latencyLine = document.getElementById("latency-line");
  const indexSizeElement = document.getElementById("stat-index-size");
  const logSizeElement = document.getElementById("stat-log-size");
  const startedElement = document.getElementById("stat-started");

  const REFRESH_INTERVAL_MS = 2000;

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat().format(value);
  }

  function formatMilliseconds(value) {
    return `${Number(value).toFixed(value < 10 ? 1 : 0)} ms`;
  }

  function drawLatency(samples) {
    if (!samples.length) {
      latencyChart.hidden = true;
      latencyEmpty.hidden = false;
      return;
    }

    latencyEmpty.hidden = true;
    latencyChart.hidden = false;
    const left = 44;
    const right = 620;
    const top = 24;
    const bottom = 160;
    const maximum = Math.max(1, ...samples);
    const points = samples.map((sample, index) => {
      const fraction = samples.length === 1 ? 0.5 : index / (samples.length - 1);
      const x = left + fraction * (right - left);
      const y = bottom - (sample / maximum) * (bottom - top);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    latencyLine.setAttribute("points", points.join(" "));
    latencyArea.setAttribute(
      "points",
      `${left},${bottom} ${points.join(" ")} ${right},${bottom}`,
    );
    latencyLabels.replaceChildren();
    [
      [maximum, top + 4],
      [maximum / 2, (top + bottom) / 2 + 4],
      [0, bottom + 4],
    ].forEach(([value, y]) => {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", "36");
      label.setAttribute("y", String(y));
      label.setAttribute("text-anchor", "end");
      label.textContent = Math.round(value);
      latencyLabels.append(label);
    });
  }

  async function refreshStats() {
    try {
      const response = await fetch("/api/admin/stats");
      if (!response.ok) throw new Error("stats request failed");
      const stats = await response.json();

      indexStatus.classList.toggle("offline", !stats.index_ready);
      indexStatus.querySelector("span:last-child").textContent = stats.index_ready
        ? "Index ready"
        : "Index not built";

      statElements.sentence_count.textContent = formatNumber(stats.sentence_count);
      statElements.source_count.textContent = formatNumber(stats.source_count);
      statElements.location_count.textContent = formatNumber(stats.location_count);
      indexSizeElement.textContent = formatBytes(stats.index_size_bytes);
      logSizeElement.textContent = formatBytes(stats.log_size_bytes);
      startedElement.textContent = new Date(stats.server_started_at).toLocaleString();
      p95Element.textContent = formatMilliseconds(stats.p95_latency_ms);
      averageElement.textContent = formatMilliseconds(stats.average_latency_ms);
      cacheRateElement.textContent = `${stats.cache_hit_rate.toFixed(1)}%`;
      cacheContextElement.textContent = stats.search_count
        ? `${stats.cache_hits} of ${stats.search_count} recent searches avoided SQLite`
        : "No searches measured";
      selectionsElement.textContent = formatNumber(stats.selected_completions);
      charactersElement.textContent = formatNumber(stats.characters_saved);
      cacheImpactValue.textContent = `${formatNumber(stats.cache_hits)} searches avoided`;
      cacheImpactCopy.textContent = stats.search_count
        ? `${stats.cache_hit_rate.toFixed(1)}% of the last ${stats.search_count} searches were served without candidate retrieval or scoring.`
        : "Cached results avoid repeated SQLite candidate retrieval and scoring.";
      cacheProgress.setAttribute("aria-valuenow", String(stats.cache_hit_rate));
      cacheProgressFill.style.width = `${Math.min(100, stats.cache_hit_rate)}%`;
      slowSearchesElement.textContent = formatNumber(stats.slow_searches);
      errorCountElement.textContent = formatNumber(stats.error_count);
      drawLatency(stats.latency_samples);
    } catch (error) {
      indexStatus.classList.add("offline");
      indexStatus.querySelector("span:last-child").textContent = "Unreachable";
    }
  }

  async function refreshLogs() {
    try {
      const response = await fetch("/api/admin/logs?limit=100&event=completion_selected");
      if (!response.ok) throw new Error("logs request failed");
      const data = await response.json();
      renderLogs(data.entries);
    } catch (error) {
      // Leave the previous table content in place on transient failures.
    }
  }

  function renderLogs(entries) {
    logEmpty.hidden = entries.length > 0;
    logBody.innerHTML = "";

    for (const entry of entries) {
      const row = document.createElement("tr");
      const time = new Date(entry.timestamp).toLocaleTimeString();
      const details = entry.details || {};

      row.innerHTML = `
        <td class="mono">${time}</td>
        <td class="log-query" title="${escapeHtml(details.query ?? "")}">${escapeHtml(details.query ?? "–")}</td>
        <td class="log-query" title="${escapeHtml(details.completed_sentence ?? "")}">${escapeHtml(details.completed_sentence ?? "–")}</td>
        <td class="mono">${details.rank ?? "–"}</td>
        <td class="mono">${details.search_elapsed_ms !== undefined ? formatMilliseconds(details.search_elapsed_ms) : "–"}</td>
        <td class="mono">${details.characters_saved ?? "–"}</td>
        <td class="mono">${details.occurrence_count?.toLocaleString() ?? "–"}</td>
      `;
      logBody.appendChild(row);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function refreshAll() {
    refreshStats();
    refreshLogs();
  }

  refreshAll();
  setInterval(() => {
    if (autoRefreshBox.checked) refreshAll();
  }, REFRESH_INTERVAL_MS);

  // --- Wire format benchmark (JSON vs Protobuf) ---------------------------

  function encodeVarint(value) {
    const bytes = [];
    let remaining = value;
    while (remaining > 0x7f) {
      bytes.push((remaining & 0x7f) | 0x80);
      remaining >>>= 7;
    }
    bytes.push(remaining & 0x7f);
    return bytes;
  }

  // Hand-rolled encoder for CompletionRequestProto { string query = 1; } —
  // small enough that pulling in a full Protobuf JS runtime isn't worth it.
  function encodeCompletionRequest(query) {
    const utf8 = new TextEncoder().encode(query);
    const tag = (1 << 3) | 2; // field 1, wire type 2 (length-delimited)
    return new Uint8Array([tag, ...encodeVarint(utf8.length), ...utf8]);
  }

  const wireQueryInput = document.getElementById("wire-query");
  const wireRunButton = document.getElementById("wire-run");
  const wireResult = document.getElementById("wire-result");

  wireRunButton.addEventListener("click", async () => {
    const query = wireQueryInput.value.trim();
    if (!query) return;

    wireRunButton.classList.add("loading");
    wireRunButton.disabled = true;
    wireResult.hidden = true;
    wireResult.classList.remove("tool-result-error");

    try {
      const [jsonResponse, binaryResponse] = await Promise.all([
        fetch("/api/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
        }),
        fetch("/api/completions/binary", {
          method: "POST",
          headers: { "Content-Type": "application/x-protobuf" },
          body: encodeCompletionRequest(query),
        }),
      ]);

      if (!jsonResponse.ok || !binaryResponse.ok) {
        throw new Error("One of the requests failed");
      }

      const jsonBytes = (await jsonResponse.arrayBuffer()).byteLength;
      const binaryBytes = (await binaryResponse.arrayBuffer()).byteLength;
      const savingsPercent = jsonBytes > 0
        ? Math.round((1 - binaryBytes / jsonBytes) * 100)
        : 0;

      wireResult.innerHTML = `
        <div class="wire-compare-grid">
          <div class="wire-compare-cell"><p class="wire-label">JSON</p><p class="wire-value">${jsonBytes} B</p></div>
          <div class="wire-compare-cell"><p class="wire-label">Protobuf</p><p class="wire-value">${binaryBytes} B</p></div>
          <div class="wire-compare-cell"><p class="wire-label">Savings</p><p class="wire-value wire-savings">${savingsPercent}%</p></div>
        </div>
        <p>Same query, same suggestions — Protobuf just packs it tighter for the trip.</p>
      `;
      wireResult.hidden = false;
    } catch (error) {
      wireResult.textContent = "Could not run the comparison. Is the server reachable?";
      wireResult.classList.add("tool-result-error");
      wireResult.hidden = false;
    } finally {
      wireRunButton.classList.remove("loading");
      wireRunButton.disabled = false;
    }
  });

  // --- AI mission briefing (Gemini) ---------------------------------------

  const briefingRunButton = document.getElementById("briefing-run");
  const briefingResult = document.getElementById("briefing-result");

  briefingRunButton.addEventListener("click", async () => {
    briefingRunButton.classList.add("loading");
    briefingRunButton.disabled = true;
    briefingResult.hidden = true;
    briefingResult.classList.remove("tool-result-error");

    try {
      const response = await fetch("/api/admin/mission-briefing", { method: "POST" });
      const data = await response.json();

      briefingResult.textContent = data.summary;
      briefingResult.classList.toggle("tool-result-error", !data.available);
      briefingResult.hidden = false;
    } catch (error) {
      briefingResult.textContent = "Could not reach the mission briefing endpoint.";
      briefingResult.classList.add("tool-result-error");
      briefingResult.hidden = false;
    } finally {
      briefingRunButton.classList.remove("loading");
      briefingRunButton.disabled = false;
    }
  });
})();
