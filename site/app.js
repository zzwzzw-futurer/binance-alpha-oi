const githubDataBaseUrl =
  "https://raw.githubusercontent.com/zzwzzw-futurer/binance-alpha-oi/main/site/data";
const dataSources = [
  {
    name: "Vercel",
    latestUrl: "data/latest.json",
    historyUrl: "data/history.json",
  },
  {
    name: "GitHub",
    latestUrl: `${githubDataBaseUrl}/latest.json`,
    historyUrl: `${githubDataBaseUrl}/history.json`,
  },
];

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

const changeClass = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return "";
  return number > 0 ? "change-positive" : "change-negative";
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

const scanSymbols = (scan) => {
  const matches = Array.isArray(scan.matches) ? scan.matches : [];
  if (matches.length) {
    return matches
      .map((item) => item.futuresSymbol || item.symbol)
      .filter(Boolean);
  }
  return Array.isArray(scan.symbols) ? scan.symbols.filter(Boolean) : [];
};

const buildPairStats = (history) => {
  const stats = new Map();

  history.forEach((scan) => {
    const matches = Array.isArray(scan.matches) ? scan.matches : [];
    if (matches.length) {
      matches.forEach((item) => {
        const symbol = item.futuresSymbol || item.symbol;
        if (!symbol) return;
        const current = stats.get(symbol) || { symbol, count: 0 };
        stats.set(symbol, {
          ...current,
          symbol,
          name: item.name || current.name || "",
          count: current.count + 1,
          lastSeen: scan.generatedAt,
          top10HoldersPercent: item.top10HoldersPercent || current.top10HoldersPercent,
          price: item.price || current.price,
          percentChange5m: item.percentChange5m || current.percentChange5m,
          alphaVolume5m: item.alphaVolume5m || current.alphaVolume5m,
          quoteVolumeSum: item.quoteVolumeSum || current.quoteVolumeSum,
        });
      });
      return;
    }

    scanSymbols(scan).forEach((symbol) => {
      const current = stats.get(symbol) || { symbol, count: 0 };
      stats.set(symbol, {
        ...current,
        count: current.count + 1,
        lastSeen: scan.generatedAt,
      });
    });
  });

  return [...stats.values()].sort((a, b) => {
    if (b.count !== a.count) return b.count - a.count;
    return Number(new Date(b.lastSeen)) - Number(new Date(a.lastSeen));
  });
};

const renderPairMonitorCounts = (history) => {
  const list = document.getElementById("pairMonitorList");
  const hint = document.getElementById("pairMonitorHint");
  list.replaceChildren();

  const stats = buildPairStats(history);
  hint.textContent = stats.length ? `累计 ${stats.length} 个交易对` : "暂无记录";

  if (!stats.length) {
    const empty = el("div", "pair-monitor-empty", "暂无交易对");
    list.appendChild(empty);
    return;
  }

  const maxCount = Math.max(1, ...stats.map((item) => item.count));
  stats.slice(0, 12).forEach((item, index) => {
    const row = el("article", "pair-monitor-item");
    const rank = el("span", "pair-rank", String(index + 1).padStart(2, "0"));
    const identity = el("div", "pair-identity");
    identity.appendChild(el("strong", "", item.symbol));
    identity.appendChild(el("span", "", item.name || formatDate(item.lastSeen)));

    const count = el("div", "pair-count");
    count.appendChild(el("strong", "", `${item.count}`));
    count.appendChild(el("span", "", "次"));

    const meta = el("div", "pair-meta");
    [
      ["Alpha", formatPrice(item.price)],
      ["5m", formatPercent(item.percentChange5m)],
      ["链上量", formatNumber(item.alphaVolume5m)],
      ["最近", formatDate(item.lastSeen)],
    ].forEach(([label, value], metaIndex) => {
      const node = el("span", metaIndex === 1 ? changeClass(item.percentChange5m) : "");
      node.textContent = `${label} ${value}`;
      meta.appendChild(node);
    });

    const meter = el("div", "pair-meter");
    const fill = el("span");
    fill.style.width = `${Math.max(8, (item.count / maxCount) * 100)}%`;
    meter.appendChild(fill);

    row.append(rank, identity, count, meta, meter);
    list.appendChild(row);
  });
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
      if (index === 4 && matches.length) {
        cell.classList.add(...changeText.split(", ").map((_, i) => {
          const item = matches[i];
          return changeClass(item?.percentChange5m);
        }).filter(Boolean).slice(0, 1));
      }
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
      const strong = el("strong", label === "5m涨跌" ? changeClass(item.percentChange5m) : "", value);
      box.appendChild(strong);
      grid.appendChild(box);
    });

    const strip = el("div", "volume-strip");
    (item.quoteVolumes1m || []).forEach((volume) => {
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

const loadJson = async (url, timeoutMs = 6000) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const separator = url.includes("?") ? "&" : "?";
  const response = await fetch(`${url}${separator}t=${Date.now()}`, {
    cache: "no-store",
    signal: controller.signal,
  });
  clearTimeout(timeout);
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
};

const loadSource = async (source) => {
  const [latest, history] = await Promise.all([
    loadJson(source.latestUrl),
    loadJson(source.historyUrl),
  ]);
  return {
    name: source.name,
    latest,
    history: Array.isArray(history) ? history : [],
  };
};

const dataTime = (payload) => {
  const ms = Number(payload?.generatedAtMs);
  if (Number.isFinite(ms)) return ms;
  const parsed = Number(new Date(payload?.generatedAt));
  return Number.isFinite(parsed) ? parsed : 0;
};

const chooseFreshestSource = (loaded) => {
  return loaded.sort((a, b) => dataTime(b.latest) - dataTime(a.latest))[0];
};

const refresh = async () => {
  try {
    const settled = await Promise.allSettled(dataSources.map(loadSource));
    const loaded = settled
      .filter((item) => item.status === "fulfilled")
      .map((item) => item.value);

    if (!loaded.length) {
      throw new Error("No data source available");
    }

    const { name, latest, history } = chooseFreshestSource(loaded);
    renderMetrics(latest);
    renderHistory(history);
    renderPairMonitorCounts(history);
    renderHistoryDetails(history);
    renderResults(Array.isArray(latest.matches) ? latest.matches : []);
    setStatus(name === "GitHub" ? "已同步 GitHub" : "已同步", "ok");
  } catch (error) {
    setStatus("同步失败", "error");
    console.error(error);
  }
};

refresh();
setInterval(refresh, 60000);
