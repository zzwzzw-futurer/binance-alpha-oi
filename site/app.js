const latestUrl = "data/latest.json";
const historyUrl = "data/history.json";

const formatNumber = (value, digits = 0) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(number);
};

const formatPrice = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (number >= 1) return formatNumber(number, 4);
  return formatNumber(number, 8);
};

const formatPercent = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  const sign = number > 0 ? "+" : "";
  return `${sign}${formatNumber(number, 2)}%`;
};

const formatDate = (iso) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const setStatus = (text, kind = "ok") => {
  document.getElementById("syncStatus").textContent = text;
  const dot = document.getElementById("syncDot");
  dot.className = `dot dot-${kind}`;
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const renderMetrics = (latest) => {
  const filters = latest.filters || {};
  const summary = latest.summary || {};
  document.getElementById("matchCount").textContent = summary.matchCount ?? "--";
  document.getElementById("freshCount").textContent = summary.freshAlertCount ?? "--";
  document.getElementById("updatedAt").textContent = formatDate(latest.generatedAt);
  document.getElementById("threshold").textContent =
    `${formatNumber(filters.futuresMinuteQuoteVolume)} / min`;
};

const renderHistory = (history) => {
  const bars = document.getElementById("historyBars");
  bars.replaceChildren();

  const recent = history.slice(-96);
  const max = Math.max(1, ...recent.map((item) => Number(item.matchCount) || 0));
  recent.forEach((item) => {
    const count = Number(item.matchCount) || 0;
    const bar = el("div", count > 0 ? "bar" : "bar bar-zero");
    bar.style.height = `${Math.max(4, (count / max) * 72)}px`;
    bar.title = `${formatDate(item.generatedAt)} 命中 ${count}`;
    bars.appendChild(bar);
  });

  const range = document.getElementById("historyRange");
  if (recent.length) {
    range.textContent = `${recent.length} 轮 / ${formatDate(recent[0].generatedAt)} - ${formatDate(recent.at(-1).generatedAt)}`;
  } else {
    range.textContent = "--";
  }
};

const renderHistoryDetails = (history) => {
  const rows = document.getElementById("historyRows");
  const hint = document.getElementById("historyDetailHint");
  rows.replaceChildren();

  const recent = history.slice(-50).reverse();
  hint.textContent = recent.length ? `最近 ${recent.length} 轮` : "--";

  recent.forEach((scan) => {
    const matches = Array.isArray(scan.matches) ? scan.matches : [];
    const symbols = matches.length
      ? matches.map((item) => item.futuresSymbol || item.symbol).filter(Boolean)
      : (Array.isArray(scan.symbols) ? scan.symbols : []);

    const row = document.createElement("tr");
    const symbolText = symbols.length ? symbols.join(", ") : "无";
    const top10Text = matches.length
      ? matches.map((item) => `${formatNumber(item.top10HoldersPercent, 2)}%`).join(", ")
      : "--";
    const volumeText = matches.length
      ? matches.map((item) => formatNumber(item.quoteVolumeSum)).join(", ")
      : "--";
    const priceText = matches.length
      ? matches.map((item) => formatPrice(item.price)).join(", ")
      : "--";
    const changeText = matches.length
      ? matches.map((item) => formatPercent(item.percentChange5m)).join(", ")
      : "--";
    const alphaVolumeText = matches.length
      ? matches.map((item) => formatNumber(item.alphaVolume5m)).join(", ")
      : "--";

    [
      formatDate(scan.generatedAt),
      symbolText,
      top10Text,
      priceText,
      changeText,
      alphaVolumeText,
      volumeText,
    ].forEach((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      cell.textContent = value;
      if (index === 1) cell.className = "history-symbols";
      row.appendChild(cell);
    });

    if ((Number(scan.freshAlertCount) || 0) > 0) {
      row.classList.add("history-row-alert");
    }
    rows.appendChild(row);
  });
};

const renderResults = (matches) => {
  const list = document.getElementById("resultList");
  const empty = document.getElementById("emptyState");
  const hint = document.getElementById("resultHint");
  list.replaceChildren();
  empty.hidden = matches.length > 0;
  hint.textContent = matches.length ? `${matches.length} 个交易对` : "无命中";

  matches.forEach((item) => {
    const card = el("article", "result-card");
    const head = el("div", "result-head");
    const symbol = el("div", "symbol");
    symbol.appendChild(el("strong", "", item.symbol || item.futuresSymbol));
    symbol.appendChild(el("span", "", item.name || item.futuresSymbol));
    const badge = el("span", "badge", item.isFreshAlert ? "新推送" : "监控中");
    head.append(symbol, badge);

    const grid = el("div", "data-grid");
    [
      ["合约", item.futuresSymbol],
      ["Top10", `${formatNumber(item.top10HoldersPercent, 2)}%`],
      ["Alpha价格", formatPrice(item.price)],
      ["5m涨跌", formatPercent(item.percentChange5m)],
      ["5m链上量", formatNumber(item.alphaVolume5m)],
      ["合约5m量", formatNumber(item.quoteVolumeSum)],
    ].forEach(([label, value]) => {
      const box = el("div");
      box.appendChild(el("span", "", label));
      box.appendChild(el("strong", "", value));
      grid.appendChild(box);
    });

    const strip = el("div", "volume-strip");
    item.quoteVolumes1m.forEach((volume) => {
      strip.appendChild(el("span", "", formatNumber(volume)));
    });

    const contract = el("div", "contract", `${item.chainId} / ${item.contractAddress}`);
    const futures = el("a", "", "打开合约");
    futures.href = item.futuresUrl;
    futures.target = "_blank";
    futures.rel = "noreferrer";

    card.append(head, grid, strip, contract, futures);
    list.appendChild(card);
  });
};

const loadJson = async (url) => {
  const response = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
};

const refresh = async () => {
  try {
    const [latest, history] = await Promise.all([loadJson(latestUrl), loadJson(historyUrl)]);
    renderMetrics(latest);
    renderHistory(Array.isArray(history) ? history : []);
    renderHistoryDetails(Array.isArray(history) ? history : []);
    renderResults(Array.isArray(latest.matches) ? latest.matches : []);
    setStatus("已同步", "ok");
  } catch (error) {
    setStatus("同步失败", "error");
    console.error(error);
  }
};

refresh();
setInterval(refresh, 60000);
