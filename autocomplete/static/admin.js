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

  function eventBadgeClass(event) {
    if (event.includes("error")) return "event-badge event-error";
    if (event.includes("rejected")) return "event-badge event-rejected";
    return "event-badge";
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
    } catch (error) {
      indexStatus.classList.add("offline");
      indexStatus.querySelector("span:last-child").textContent = "Unreachable";
    }
  }

  async function refreshLogs() {
    try {
      const response = await fetch("/api/admin/logs?limit=100");
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
        <td><span class="${eventBadgeClass(entry.event)}">${entry.event}</span></td>
        <td class="log-query" title="${escapeHtml(details.query ?? "")}">${escapeHtml(details.query ?? details.reason ?? "–")}</td>
        <td class="mono">${details.elapsed_ms !== undefined ? `${details.elapsed_ms} ms` : "–"}</td>
        <td class="mono">${details.suggestion_count !== undefined ? details.suggestion_count : "–"}</td>
        <td class="mono">${escapeHtml(details.client ?? "–")}</td>
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

  // --- AI health check (Gemini) -------------------------------------------

  const healthRunButton = document.getElementById("health-run");
  const healthResult = document.getElementById("health-result");

  healthRunButton.addEventListener("click", async () => {
    healthRunButton.classList.add("loading");
    healthRunButton.disabled = true;
    healthResult.hidden = true;
    healthResult.classList.remove("tool-result-error");

    try {
      const response = await fetch("/api/admin/health-check", { method: "POST" });
      const data = await response.json();

      healthResult.textContent = data.summary;
      healthResult.classList.toggle("tool-result-error", !data.available);
      healthResult.hidden = false;
    } catch (error) {
      healthResult.textContent = "Could not reach the health check endpoint.";
      healthResult.classList.add("tool-result-error");
      healthResult.hidden = false;
    } finally {
      healthRunButton.classList.remove("loading");
      healthRunButton.disabled = false;
    }
  });
})();
