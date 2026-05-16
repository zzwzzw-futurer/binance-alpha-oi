#!/usr/bin/env python3
"""Monitor Binance Alpha tokens that also have hot Binance USD-M futures flow.

Data sources follow the installed Binance skills documentation:
- crypto-market-rank: Binance Web3 Alpha unified rank list
- query-token-info: holder concentration fields from Alpha/Dynamic data
- binance futures-usds: public USD-M futures exchangeInfo and 1m klines

The script is dependency-free and reads configuration from environment variables
or a local .env file in this directory.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent

ALPHA_RANK_URL = (
    "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/"
    "wallet/market/token/pulse/unified/rank/list/ai"
)
TOKEN_DYNAMIC_URL = (
    "https://web3.binance.com/bapi/defi/v4/public/wallet-direct/buw/"
    "wallet/market/token/dynamic/info/ai"
)
FUTURES_BASE_URL = "https://fapi.binance.com"
TELEGRAM_BASE_URL = "https://api.telegram.org"
GITHUB_API_BASE_URL = "https://api.github.com"

ALPHA_HEADERS = {
    "Accept-Encoding": "identity",
    "Content-Type": "application/json",
    "User-Agent": "binance-web3/2.1 (Skill)",
}
TOKEN_HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "binance-web3/1.1 (Skill)",
}
FUTURES_HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "binance-cli-compatible-alpha-monitor/1.0",
}


def load_dotenv(path: Path, override: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if override or key not in os.environ:
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if not value:
        return default
    return int(value)


def env_decimal(name: str, default: str) -> Decimal:
    value = env_str(name, default)
    return Decimal(value)


def env_bool(name: str, default: bool) -> bool:
    value = env_str(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def decimal_fmt(value: Decimal, places: int = 2) -> str:
    quant = Decimal(1).scaleb(-places)
    return f"{value.quantize(quant):,}"


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def local_timestamp() -> str:
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 2,
) -> Any:
    payload = None
    req_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url=url,
                data=payload,
                headers=req_headers,
                method=method.upper(),
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read().decode("utf-8")
            return json.loads(data)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                sleep_for = 0.75 * (attempt + 1)
                time.sleep(sleep_for)
                continue
            raise RuntimeError(f"{method} {url} failed: {exc}") from exc
    raise RuntimeError(f"{method} {url} failed: {last_error}")


def fetch_alpha_tokens(
    page_size: int,
    max_pages: int,
    alpha_period: int,
    top10_threshold: Decimal,
    chain_ids: List[str],
) -> List[Dict[str, Any]]:
    tokens: Dict[Tuple[str, str], Dict[str, Any]] = {}
    chains_to_fetch: List[Optional[str]] = chain_ids or [None]

    for chain_id in chains_to_fetch:
        for page in range(1, max_pages + 1):
            body: Dict[str, Any] = {
                "rankType": 20,
                "period": alpha_period,
                "page": page,
                "size": page_size,
                "holdersTop10PercentMin": str(top10_threshold),
            }
            if chain_id:
                body["chainId"] = chain_id

            response = request_json("POST", ALPHA_RANK_URL, ALPHA_HEADERS, body)
            if not response.get("success"):
                raise RuntimeError(f"Alpha rank API returned {response!r}")
            page_tokens = response.get("data", {}).get("tokens") or []
            for token in page_tokens:
                key = (
                    str(token.get("chainId") or ""),
                    str(token.get("contractAddress") or "").lower(),
                )
                if key[0] and key[1]:
                    tokens[key] = token
            total = int(response.get("data", {}).get("total") or 0)
            if not page_tokens or page * page_size >= total:
                break

    return list(tokens.values())


def fetch_token_dynamic(chain_id: str, contract_address: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode(
        {"chainId": chain_id, "contractAddress": contract_address}
    )
    response = request_json(
        "GET", f"{TOKEN_DYNAMIC_URL}?{query}", TOKEN_HEADERS, timeout=20
    )
    if not response.get("success"):
        return {}
    return response.get("data") or {}


def normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper() if ch.isalnum())


def fetch_futures_symbols(quote_asset: str) -> Dict[str, List[Dict[str, Any]]]:
    response = request_json(
        "GET", f"{FUTURES_BASE_URL}/fapi/v1/exchangeInfo", FUTURES_HEADERS, timeout=20
    )
    symbols: Dict[str, List[Dict[str, Any]]] = {}
    for item in response.get("symbols", []):
        if item.get("status") != "TRADING":
            continue
        if item.get("contractType") != "PERPETUAL":
            continue
        if item.get("quoteAsset") != quote_asset:
            continue
        base = normalize_symbol(str(item.get("baseAsset") or ""))
        if not base:
            continue
        symbols.setdefault(base, []).append(item)
    return symbols


def candidate_futures_symbols(
    alpha_symbol: str,
    futures_by_base: Dict[str, List[Dict[str, Any]]],
    symbol_prefixes: List[str],
) -> List[Dict[str, Any]]:
    normalized = normalize_symbol(alpha_symbol)
    if not normalized:
        return []

    candidates: List[Dict[str, Any]] = []
    seen = set()
    for prefix in symbol_prefixes:
        base = normalize_symbol(f"{prefix}{normalized}")
        for item in futures_by_base.get(base, []):
            symbol = item.get("symbol")
            if symbol and symbol not in seen:
                candidates.append(item)
                seen.add(symbol)
    return candidates


def fetch_futures_1m_klines(symbol: str, limit: int) -> List[List[Any]]:
    query = urllib.parse.urlencode({"symbol": symbol, "interval": "1m", "limit": limit})
    response = request_json(
        "GET", f"{FUTURES_BASE_URL}/fapi/v1/klines?{query}", FUTURES_HEADERS, timeout=20
    )
    if not isinstance(response, list):
        raise RuntimeError(f"Unexpected kline response for {symbol}: {response!r}")
    return response


def last_closed_quote_volumes(klines: List[List[Any]], minutes: int) -> List[Decimal]:
    current_open_ms = int(time.time() // 60 * 60 * 1000)
    closed = [row for row in klines if int(row[0]) < current_open_ms]
    selected = closed[-minutes:]
    return [to_decimal(row[7]) for row in selected]


def futures_volume_passes(
    symbol: str,
    minutes: int,
    min_quote_volume: Decimal,
    require_all_minutes: bool,
    kline_limit: int,
) -> Tuple[bool, List[Decimal]]:
    klines = fetch_futures_1m_klines(symbol, kline_limit)
    volumes = last_closed_quote_volumes(klines, minutes)
    if len(volumes) < minutes:
        return False, volumes
    if require_all_minutes:
        return all(volume >= min_quote_volume for volume in volumes), volumes
    return sum(volumes, Decimal("0")) >= min_quote_volume * Decimal(minutes), volumes


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"alerts": {}}
    try:
        with path.open("r", encoding="utf-8") as file_handle:
            state = json.load(file_handle)
    except (OSError, json.JSONDecodeError):
        return {"alerts": {}}
    if "alerts" not in state:
        state["alerts"] = {}
    return state


def save_state(path: Path, state: Dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file_handle:
        json.dump(state, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.replace(path)


def alert_key(match: Dict[str, Any]) -> str:
    token = match["token"]
    return "|".join(
        [
            str(token.get("chainId") or ""),
            str(token.get("contractAddress") or "").lower(),
            str(match["futures_symbol"]),
        ]
    )


def filter_cooldown(
    matches: List[Dict[str, Any]],
    state: Dict[str, Any],
    cooldown_seconds: int,
) -> List[Dict[str, Any]]:
    if cooldown_seconds <= 0:
        return matches
    now_ts = int(time.time())
    fresh: List[Dict[str, Any]] = []
    alerts = state.setdefault("alerts", {})
    for match in matches:
        key = alert_key(match)
        last_sent = int(alerts.get(key, 0) or 0)
        if now_ts - last_sent >= cooldown_seconds:
            fresh.append(match)
    return fresh


def mark_alerted(matches: List[Dict[str, Any]], state: Dict[str, Any]) -> None:
    now_ts = int(time.time())
    alerts = state.setdefault("alerts", {})
    for match in matches:
        alerts[alert_key(match)] = now_ts


def scan_once(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    alpha_tokens = fetch_alpha_tokens(
        page_size=config["alpha_page_size"],
        max_pages=config["alpha_max_pages"],
        alpha_period=config["alpha_period"],
        top10_threshold=config["top10_threshold"],
        chain_ids=config["alpha_chains"],
    )
    logging.info("Fetched %d Alpha tokens after top10 prefilter", len(alpha_tokens))

    futures_by_base = fetch_futures_symbols(config["futures_quote_asset"])
    futures_count = sum(len(items) for items in futures_by_base.values())
    logging.info("Fetched %d USD-M perpetual futures symbols", futures_count)

    candidates: List[Tuple[Dict[str, Any], Dict[str, Any], Decimal]] = []
    for token in alpha_tokens:
        top10 = to_decimal(
            token.get("holdersTop10Percent")
            or token.get("top10HoldersPercentage")
            or token.get("holdersTop10Percentage")
        )
        if not top10 and token.get("chainId") and token.get("contractAddress"):
            dynamic = fetch_token_dynamic(
                str(token.get("chainId")), str(token.get("contractAddress"))
            )
            top10 = to_decimal(
                dynamic.get("top10HoldersPercentage")
                or dynamic.get("holdersTop10Percent")
            )
            token["_dynamic"] = dynamic
        if top10 < config["top10_threshold"]:
            continue

        for futures in candidate_futures_symbols(
            str(token.get("symbol") or ""),
            futures_by_base,
            config["symbol_prefixes"],
        ):
            candidates.append((token, futures, top10))

    logging.info("Found %d Alpha token/futures candidates", len(candidates))
    matches: List[Dict[str, Any]] = []

    def check_candidate(candidate: Tuple[Dict[str, Any], Dict[str, Any], Decimal]) -> Optional[Dict[str, Any]]:
        token, futures, top10 = candidate
        symbol = str(futures.get("symbol") or "")
        try:
            passed, volumes = futures_volume_passes(
                symbol=symbol,
                minutes=config["futures_minutes"],
                min_quote_volume=config["futures_min_quote_volume"],
                require_all_minutes=config["futures_require_all_minutes"],
                kline_limit=config["futures_kline_limit"],
            )
        except Exception as exc:  # noqa: BLE001 - keep the monitor alive per symbol
            logging.warning("Failed to check futures klines for %s: %s", symbol, exc)
            return None
        if not passed:
            return None
        return {
            "token": token,
            "top10": top10,
            "futures_symbol": symbol,
            "futures_base": futures.get("baseAsset"),
            "quote_volumes": volumes,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=config["max_workers"]) as pool:
        for result in pool.map(check_candidate, candidates):
            if result:
                matches.append(result)

    matches.sort(
        key=lambda item: (min(item["quote_volumes"]), item["top10"]),
        reverse=True,
    )
    return matches


def format_match(match: Dict[str, Any], index: int) -> str:
    token = match["token"]
    symbol = html.escape(str(token.get("symbol") or ""))
    name = html.escape(str(token.get("metaInfo", {}).get("name") or token.get("name") or ""))
    chain_id = html.escape(str(token.get("chainId") or ""))
    contract = html.escape(str(token.get("contractAddress") or ""))
    futures_symbol = html.escape(str(match["futures_symbol"]))
    top10 = decimal_fmt(match["top10"], 2)
    volumes = ", ".join(decimal_fmt(volume, 0) for volume in match["quote_volumes"])
    price = to_decimal(token.get("price"))
    price_text = decimal_fmt(price, 8) if price else "n/a"
    change_5m = html.escape(str(token.get("percentChange5m") or "n/a"))
    volume_5m = decimal_fmt(to_decimal(token.get("volume5m")), 0)
    futures_url = f"https://www.binance.com/en/futures/{urllib.parse.quote(str(match['futures_symbol']))}"

    return (
        f"{index}. <b>{symbol}</b>{f' ({name})' if name else ''}\n"
        f"合约: <a href=\"{futures_url}\">{futures_symbol}</a>\n"
        f"链/合约地址: <code>{chain_id}</code> / <code>{contract}</code>\n"
        f"Top10 持仓: <b>{top10}%</b>\n"
        f"近 {len(match['quote_volumes'])} 根 1m 合约成交额: <code>{html.escape(volumes)}</code> USDT\n"
        f"Alpha 价格: <code>{html.escape(price_text)}</code>, 5m涨跌: <code>{change_5m}%</code>, "
        f"5m链上量: <code>{html.escape(volume_5m)}</code> USD"
    )


def build_message(matches: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
    threshold = decimal_fmt(config["futures_min_quote_volume"], 0)
    top10 = decimal_fmt(config["top10_threshold"], 2)
    mode = "每分钟均大于" if config["futures_require_all_minutes"] else "5分钟均值大于"
    header = (
        "<b>Binance Alpha 合约异动监控</b>\n"
        f"时间: <code>{html.escape(local_timestamp())}</code>\n"
        f"命中: <b>{len(matches)}</b>\n"
        f"条件: Alpha + 已上线 USD-M 永续 + Top10>{top10}% + "
        f"近{config['futures_minutes']}分钟{mode}{threshold} USDT\n"
    )
    if not matches:
        return header + "\n本轮无命中。"
    parts = [header]
    for index, match in enumerate(matches, start=1):
        parts.append(format_match(match, index))
    return "\n\n".join(parts)


def split_telegram_message(message: str, max_chars: int = 3900) -> List[str]:
    if len(message) <= max_chars:
        return [message]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for block in message.split("\n\n"):
        block_len = len(block) + 2
        if current and current_len + block_len > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def send_telegram(message: str, token: str, chat_id: str, dry_run: bool) -> None:
    if dry_run:
        print(message)
        return
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    url = f"{TELEGRAM_BASE_URL}/bot{token}/sendMessage"
    for chunk in split_telegram_message(message):
        body = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        response = request_json("POST", url, {"Content-Type": "application/json"}, body)
        if not response.get("ok"):
            raise RuntimeError(f"Telegram send failed: {response!r}")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
        file_handle.write("\n")
    tmp_path.replace(path)


def read_json_array(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def compact_token_links(token: Dict[str, Any]) -> List[Dict[str, str]]:
    links = token.get("links") or []
    compacted = []
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            link = str(item.get("link") or "").strip()
            if label and link:
                compacted.append({"label": label, "link": link})
    return compacted


def match_to_web_item(match: Dict[str, Any], fresh_keys: set) -> Dict[str, Any]:
    token = match["token"]
    volumes = match["quote_volumes"]
    top10 = match["top10"]
    futures_symbol = str(match["futures_symbol"])
    key = alert_key(match)
    name = token.get("metaInfo", {}).get("name") or token.get("name") or ""
    quote_sum = sum(volumes, Decimal("0"))
    quote_min = min(volumes) if volumes else Decimal("0")

    return {
        "key": key,
        "isFreshAlert": key in fresh_keys,
        "symbol": str(token.get("symbol") or ""),
        "name": str(name),
        "chainId": str(token.get("chainId") or ""),
        "contractAddress": str(token.get("contractAddress") or ""),
        "futuresSymbol": futures_symbol,
        "futuresUrl": f"https://www.binance.com/en/futures/{urllib.parse.quote(futures_symbol)}",
        "top10HoldersPercent": str(top10),
        "price": str(token.get("price") or ""),
        "percentChange5m": str(token.get("percentChange5m") or ""),
        "alphaVolume5m": str(token.get("volume5m") or ""),
        "marketCap": str(token.get("marketCap") or ""),
        "liquidity": str(token.get("liquidity") or ""),
        "holders": str(token.get("holders") or ""),
        "quoteVolumes1m": [str(volume) for volume in volumes],
        "quoteVolumeMin": str(quote_min),
        "quoteVolumeSum": str(quote_sum),
        "links": compact_token_links(token),
    }


def build_web_payload(
    matches: List[Dict[str, Any]],
    fresh_matches: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    generated_at = now_utc()
    fresh_keys = {alert_key(match) for match in fresh_matches}
    items = [match_to_web_item(match, fresh_keys) for match in matches]
    return {
        "status": "ok",
        "generatedAt": generated_at.isoformat(),
        "generatedAtMs": int(generated_at.timestamp() * 1000),
        "localTime": local_timestamp(),
        "summary": {
            "matchCount": len(matches),
            "freshAlertCount": len(fresh_matches),
            "symbols": [item["futuresSymbol"] for item in items],
        },
        "filters": {
            "alphaRankType": 20,
            "alphaPeriod": config["alpha_period"],
            "alphaChains": config["alpha_chains"],
            "top10Threshold": str(config["top10_threshold"]),
            "futuresQuoteAsset": config["futures_quote_asset"],
            "futuresMinutes": config["futures_minutes"],
            "futuresMinuteQuoteVolume": str(config["futures_min_quote_volume"]),
            "futuresRequireAllMinutes": config["futures_require_all_minutes"],
        },
        "telegram": {
            "sendEmptyResult": config["send_empty_result"],
            "alertCooldownSeconds": config["alert_cooldown_seconds"],
        },
        "matches": items,
    }


def build_history_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = payload["summary"]
    history_matches = []
    for item in payload.get("matches", []):
        history_matches.append(
            {
                "symbol": item["symbol"],
                "name": item["name"],
                "chainId": item["chainId"],
                "contractAddress": item["contractAddress"],
                "futuresSymbol": item["futuresSymbol"],
                "top10HoldersPercent": item["top10HoldersPercent"],
                "price": item["price"],
                "percentChange5m": item["percentChange5m"],
                "alphaVolume5m": item["alphaVolume5m"],
                "quoteVolumeMin": item["quoteVolumeMin"],
                "quoteVolumeSum": item["quoteVolumeSum"],
                "isFreshAlert": item["isFreshAlert"],
            }
        )
    return {
        "generatedAt": payload["generatedAt"],
        "generatedAtMs": payload["generatedAtMs"],
        "localTime": payload["localTime"],
        "matchCount": summary["matchCount"],
        "freshAlertCount": summary["freshAlertCount"],
        "symbols": summary["symbols"],
        "matches": history_matches,
    }


def post_webhook(url: str, payload: Dict[str, Any], token: str, timeout: int) -> None:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "binance-alpha-oi-monitor/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status >= 400:
            raise RuntimeError(f"Webhook returned HTTP {response.status}")
        response.read()


def github_headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "binance-alpha-oi-monitor/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_request_json(
    method: str,
    url: str,
    token: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers=github_headers(token),
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub {method} {url} failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub {method} {url} failed: {exc}") from exc
    if not response_body:
        return {}
    return json.loads(response_body)


def normalize_repo_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip("/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    return "/".join(parts)


def default_github_path_prefix(output_dir: Path) -> str:
    try:
        relative = output_dir.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return ""
    return relative.as_posix()


def github_commit_data_files(
    files: List[Tuple[str, Path]],
    config: Dict[str, Any],
) -> Optional[str]:
    if not config["github_sync_enabled"]:
        return None

    token = config["github_sync_token"]
    repository = config["github_sync_repository"]
    branch = config["github_sync_branch"]
    if not token:
        logging.debug("GITHUB_SYNC_TOKEN is not set; skipping GitHub sync")
        return None
    if not repository or repository.count("/") != 1:
        raise RuntimeError("GITHUB_SYNC_REPOSITORY must be in owner/repo format")
    if not branch:
        raise RuntimeError("GITHUB_SYNC_BRANCH is required")

    api_base = config["github_sync_api_base_url"].rstrip("/")
    timeout = config["github_sync_timeout"]
    branch_ref = urllib.parse.quote(f"heads/{branch}", safe="/")
    repo_url = f"{api_base}/repos/{repository}"

    ref = github_request_json(
        "GET",
        f"{repo_url}/git/ref/{branch_ref}",
        token,
        timeout=timeout,
    )
    parent_sha = str(ref.get("object", {}).get("sha") or "")
    if not parent_sha:
        raise RuntimeError("Could not resolve GitHub branch head")

    parent_commit = github_request_json(
        "GET",
        f"{repo_url}/git/commits/{parent_sha}",
        token,
        timeout=timeout,
    )
    base_tree_sha = str(parent_commit.get("tree", {}).get("sha") or "")
    if not base_tree_sha:
        raise RuntimeError("Could not resolve GitHub base tree")

    tree = []
    for repo_path, local_path in files:
        content = local_path.read_text(encoding="utf-8")
        blob = github_request_json(
            "POST",
            f"{repo_url}/git/blobs",
            token,
            {"content": content, "encoding": "utf-8"},
            timeout=timeout,
        )
        blob_sha = str(blob.get("sha") or "")
        if not blob_sha:
            raise RuntimeError(f"Could not create GitHub blob for {repo_path}")
        tree.append(
            {
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }
        )

    new_tree = github_request_json(
        "POST",
        f"{repo_url}/git/trees",
        token,
        {"base_tree": base_tree_sha, "tree": tree},
        timeout=timeout,
    )
    tree_sha = str(new_tree.get("sha") or "")
    if not tree_sha:
        raise RuntimeError("Could not create GitHub tree")

    commit_body: Dict[str, Any] = {
        "message": config["github_sync_commit_message"],
        "tree": tree_sha,
        "parents": [parent_sha],
    }
    committer_name = config["github_sync_committer_name"]
    committer_email = config["github_sync_committer_email"]
    if committer_name and committer_email:
        commit_body["committer"] = {
            "name": committer_name,
            "email": committer_email,
        }

    commit = github_request_json(
        "POST",
        f"{repo_url}/git/commits",
        token,
        commit_body,
        timeout=timeout,
    )
    commit_sha = str(commit.get("sha") or "")
    if not commit_sha:
        raise RuntimeError("Could not create GitHub commit")

    github_request_json(
        "PATCH",
        f"{repo_url}/git/refs/{branch_ref}",
        token,
        {"sha": commit_sha, "force": False},
        timeout=timeout,
    )
    return commit_sha


def sync_github_data_files(
    latest_path: Path,
    history_path: Path,
    config: Dict[str, Any],
    dry_run: bool,
) -> None:
    if dry_run:
        logging.info("Skipping GitHub sync during dry-run")
        return
    path_prefix = normalize_repo_path(config["github_sync_path_prefix"])
    if not path_prefix:
        path_prefix = default_github_path_prefix(config["web_output_dir"])
    if not path_prefix:
        raise RuntimeError("GITHUB_SYNC_PATH_PREFIX is required for GitHub sync")
    files = [
        (normalize_repo_path(f"{path_prefix}/latest.json"), latest_path),
        (normalize_repo_path(f"{path_prefix}/history.json"), history_path),
    ]
    commit_sha = github_commit_data_files(files, config)
    if commit_sha:
        logging.info(
            "Synced website data to GitHub %s@%s commit=%s",
            config["github_sync_repository"],
            config["github_sync_branch"],
            commit_sha,
        )


def sync_web_payload(payload: Dict[str, Any], config: Dict[str, Any], dry_run: bool) -> None:
    if not config["web_sync_enabled"]:
        return

    output_dir = config["web_output_dir"]
    latest_path = output_dir / "latest.json"
    history_path = output_dir / "history.json"
    atomic_write_json(latest_path, payload)

    history = read_json_array(history_path)
    history.append(build_history_item(payload))
    history = history[-config["web_history_limit"] :]
    atomic_write_json(history_path, history)
    logging.info("Wrote website sync data to %s", output_dir)

    try:
        sync_github_data_files(latest_path, history_path, config, dry_run)
    except Exception as exc:  # noqa: BLE001 - GitHub sync should not block alerts
        logging.warning("GitHub data sync failed: %s", exc)

    webhook_url = config["web_sync_webhook_url"]
    if webhook_url:
        post_webhook(
            url=webhook_url,
            payload=payload,
            token=config["web_sync_webhook_token"],
            timeout=config["web_sync_webhook_timeout"],
        )
        logging.info("Posted website sync payload to webhook")


def build_config() -> Dict[str, Any]:
    load_dotenv(ROOT / ".env", override=True)
    prefixes_raw = os.environ.get("FUTURES_SYMBOL_PREFIXES", ",1000,10000,1000000,1M")
    symbol_prefixes = [item.strip() for item in prefixes_raw.split(",")]
    if "" not in symbol_prefixes:
        symbol_prefixes.insert(0, "")
    state_file = Path(
        env_str("STATE_FILE", str(ROOT / ".alpha_futures_monitor_state.json"))
    )
    if not state_file.is_absolute():
        state_file = ROOT / state_file
    web_output_dir = Path(env_str("WEB_OUTPUT_DIR", str(ROOT / "site" / "data")))
    if not web_output_dir.is_absolute():
        web_output_dir = ROOT / web_output_dir

    return {
        "telegram_bot_token": env_str("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": env_str("TELEGRAM_CHAT_ID"),
        "scan_interval_seconds": env_int("SCAN_INTERVAL_SECONDS", 300),
        "alpha_page_size": env_int("ALPHA_PAGE_SIZE", 200),
        "alpha_max_pages": env_int("ALPHA_MAX_PAGES", 10),
        "alpha_period": env_int("ALPHA_PERIOD", 20),
        "alpha_chains": split_csv(env_str("ALPHA_CHAINS", "")),
        "top10_threshold": env_decimal("TOP10_THRESHOLD", "80"),
        "futures_quote_asset": env_str("FUTURES_QUOTE_ASSET", "USDT"),
        "futures_minutes": env_int("FUTURES_MINUTES", 5),
        "futures_min_quote_volume": env_decimal("FUTURES_MINUTE_QUOTE_VOLUME", "300000"),
        "futures_require_all_minutes": env_bool("FUTURES_REQUIRE_ALL_MINUTES", True),
        "futures_kline_limit": env_int("FUTURES_KLINE_LIMIT", 8),
        "symbol_prefixes": symbol_prefixes,
        "max_workers": env_int("MAX_WORKERS", 8),
        "state_file": state_file,
        "alert_cooldown_seconds": env_int("ALERT_COOLDOWN_SECONDS", 3600),
        "send_empty_result": env_bool("SEND_EMPTY_RESULT", False),
        "web_sync_enabled": env_bool("WEB_SYNC_ENABLED", True),
        "web_output_dir": web_output_dir,
        "web_history_limit": env_int("WEB_HISTORY_LIMIT", 288),
        "web_sync_webhook_url": env_str("WEB_SYNC_WEBHOOK_URL"),
        "web_sync_webhook_token": env_str("WEB_SYNC_WEBHOOK_TOKEN"),
        "web_sync_webhook_timeout": env_int("WEB_SYNC_WEBHOOK_TIMEOUT", 15),
        "github_sync_enabled": env_bool("GITHUB_SYNC_ENABLED", True),
        "github_sync_token": env_str("GITHUB_SYNC_TOKEN"),
        "github_sync_repository": env_str(
            "GITHUB_SYNC_REPOSITORY",
            env_str("GITHUB_REPOSITORY"),
        ),
        "github_sync_branch": env_str("GITHUB_SYNC_BRANCH", "main"),
        "github_sync_path_prefix": env_str("GITHUB_SYNC_PATH_PREFIX", "site/data"),
        "github_sync_commit_message": env_str(
            "GITHUB_SYNC_COMMIT_MESSAGE",
            "Update public scan data",
        ),
        "github_sync_api_base_url": env_str(
            "GITHUB_SYNC_API_BASE_URL",
            GITHUB_API_BASE_URL,
        ),
        "github_sync_timeout": env_int("GITHUB_SYNC_TIMEOUT", 20),
        "github_sync_committer_name": env_str("GITHUB_SYNC_COMMITTER_NAME"),
        "github_sync_committer_email": env_str("GITHUB_SYNC_COMMITTER_EMAIL"),
    }


def run_once(config: Dict[str, Any], dry_run: bool) -> int:
    state = load_state(config["state_file"])
    matches = scan_once(config)
    fresh_matches = filter_cooldown(matches, state, config["alert_cooldown_seconds"])
    web_payload = build_web_payload(matches, fresh_matches, config)

    try:
        sync_web_payload(web_payload, config, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - website sync should not block Telegram
        logging.warning("Website sync failed: %s", exc)

    if fresh_matches or config["send_empty_result"] or dry_run:
        message = build_message(fresh_matches if fresh_matches else matches, config)
        send_telegram(
            message=message,
            token=config["telegram_bot_token"],
            chat_id=config["telegram_chat_id"],
            dry_run=dry_run,
        )
        if fresh_matches and not dry_run:
            mark_alerted(fresh_matches, state)
            save_state(config["state_file"], state)
    else:
        logging.info(
            "No fresh matches to alert. total_matches=%d cooldown=%ds",
            len(matches),
            config["alert_cooldown_seconds"],
        )
    return len(fresh_matches)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Binance Alpha tokens and push Telegram alerts."
    )
    parser.add_argument("--once", action="store_true", help="Run one scan then exit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Telegram message instead of sending it.",
    )
    parser.add_argument(
        "--log-level",
        default=env_str("LOG_LEVEL", "INFO"),
        help="Python logging level, default INFO.",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    load_dotenv(ROOT / ".env", override=True)
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    while True:
        config = build_config()
        started = time.monotonic()
        try:
            alerted = run_once(config, dry_run=args.dry_run)
            logging.info("Scan finished. fresh_alerts=%d", alerted)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - long-running monitor should survive transient API errors
            logging.exception("Scan failed: %s", exc)

        if args.once:
            return 0

        elapsed = time.monotonic() - started
        sleep_for = max(1, config["scan_interval_seconds"] - int(elapsed))
        logging.info("Sleeping %ds", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130)
