# -*- coding: utf-8 -*-
"""领星数据通道客户端：优先「领星 API 服务站」，本地 auth-token 直连兜底

首选（服务站）：登录态由服务站以常驻领星账号注入请求头，客户端只带服务站自己的
  X-User-Id / X-User-Token，摆脱本地 auth-token 被顶号（-999/8003）的问题。
  POST /api/user/login {user_id, password} → {user_token, expire_in}（默认 7 天）
  POST /api/proxy {url | api_name, body, account, auto_pagination}
    → {success, total, pages, data:[...], message}

兜底（本地直连）：服务站未配置 / 网络不通 / 未绑定领星账号时，直接用 auth-token 加
  x-ak-* 头请求领星网页 API，并归一化成与服务站同款响应结构，调用方无需感知。
  鉴权失败（8003 / -999 顶号）时，接入本机 CDP 浏览器登录领星、重新捕获 auth-token
  后自动重试一次（需本机常驻浏览器: python -m browser_api.browser_starter --port 18800）。

配置（.env / app.config.Settings）：
  LX_STATION_BASE_URL / LX_STATION_USER_ID / LX_STATION_PASSWORD / LX_STATION_TIMEOUT
  LX_AUTH_TOKEN（或 LX_HEADER_AUTH_TOKEN）  本地直连 auth-token（静态种子，可留空）
  LX_HEADER_COMPANY_ID / LX_HEADER_UID / LX_HEADER_ENV_KEY  本地直连 x-ak-* 头
  LX_USERNAME / LX_PASSWORD / LX_CDP_HOST / LX_CDP_PORT  本地 CDP 刷新登录态
"""

import logging
import threading
import time

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_token: str | None = None
_expire_at: float = 0.0
_session = requests.Session()

# ── 本地直连缺省参数（.env 未配置时回退，与 scraper 内置兜底一致） ──
_DEFAULT_COMPANY_ID = "90136117059997696"
_DEFAULT_UID = "11054904"
_DEFAULT_ENV_KEY = "huizhixin"
_X_AK_VERSION = "3.8.9.3.0.185"
_MAX_PAGES = 2000  # 直连翻页上限（防死循环）
_OK_CODES = {0, 1, 200, "0", "1", "200", None}
_AUTH_FAIL_CODES = {8003, "8003"}  # 领星鉴权失败（含 -999 顶号）


class StationError(RuntimeError):
    """领星数据通道调用失败（服务站与本地直连均不可用/失败）"""


class _DirectAuthError(StationError):
    """本地直连鉴权失败：可通过 CDP 刷新 auth-token 后重试"""


# ──────────────────────────── 服务站（首选） ────────────────────────────

def station_enabled() -> bool:
    """是否已配置服务站"""
    return bool(
        settings.LX_STATION_BASE_URL
        and settings.LX_STATION_USER_ID
        and settings.LX_STATION_PASSWORD
    )


def _url(path: str) -> str:
    return settings.LX_STATION_BASE_URL.rstrip("/") + path


def _login(force: bool = False) -> str:
    """获取（并按需刷新）服务站 user_token；7 天有效，提前 60s 视为过期"""
    global _token, _expire_at
    with _lock:
        now = time.time()
        if not force and _token and now < _expire_at - 60:
            return _token
        if not station_enabled():
            raise StationError(
                "未配置领星 API 服务站，请在 .env 设置 "
                "LX_STATION_BASE_URL / LX_STATION_USER_ID / LX_STATION_PASSWORD"
            )
        creds = {"user_id": settings.LX_STATION_USER_ID, "password": settings.LX_STATION_PASSWORD}
        timeout = settings.LX_STATION_TIMEOUT

        def _do_login() -> dict:
            resp = _session.post(_url("/api/user/login"), json=creds, timeout=min(timeout, 60))
            resp.raise_for_status()
            return resp.json()

        try:
            data = _do_login()
        except (requests.RequestException, ValueError) as e:
            raise StationError(f"服务站登录请求失败: {e}")
        # 账号不存在 → 先注册再登录（幂等：已存在时注册接口返回失败，忽略即可）
        if not data.get("success"):
            try:
                _session.post(_url("/api/user/register"), json=creds, timeout=min(timeout, 60))
            except requests.RequestException:
                pass
            try:
                data = _do_login()
            except (requests.RequestException, ValueError) as e:
                raise StationError(f"服务站登录请求失败: {e}")
        if not data.get("success") or not data.get("user_token"):
            raise StationError(f"服务站登录失败: {data}")
        _token = data["user_token"]
        _expire_at = now + float(data.get("expire_in") or 604800)
        logger.info("领星服务站已登录: user=%s", settings.LX_STATION_USER_ID)
        return _token


def _station_call(api_name: str | None, url: str | None, body: dict | None,
                  account: str | None, auto_pagination: bool, timeout: float) -> dict:
    """调用服务站 POST /api/proxy，鉴权失败（401 / unauthorized）时自动重登一次。"""
    payload: dict = {}
    if api_name:
        payload["api_name"] = api_name
    if url:
        payload["url"] = url
    if body is not None:
        payload["body"] = body
    if account:
        payload["account"] = account
    if auto_pagination:
        payload["auto_pagination"] = True

    last_err = ""
    for attempt in (1, 2):
        token = _login(force=(attempt == 2))
        headers = {"X-User-Id": settings.LX_STATION_USER_ID, "X-User-Token": token}
        try:
            resp = _session.post(_url("/api/proxy"), headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise StationError(f"服务站请求失败: {e}")
        if resp.status_code == 401:
            last_err = f"HTTP 401: {resp.text[:300]}"
            continue
        if resp.status_code >= 400:
            raise StationError(f"服务站返回 HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError:
            raise StationError(f"服务站返回非 JSON: {resp.text[:300]}")
        if isinstance(data, dict) and data.get("error") == "unauthorized":
            last_err = str(data)
            continue
        return data
    raise StationError(f"服务站鉴权失败（已重登一次仍失败）: {last_err}")


# ──────────────────────────── 本地直连（兜底） ────────────────────────────

# CDP 刷新出的 token 缓存（优先于 .env 静态值；服务站不可用且本地 token 失效时刷新）
_direct_token_cache: str | None = None
_direct_lock = threading.Lock()


def _direct_token() -> str:
    return _direct_token_cache or settings.LX_HEADER_AUTH_TOKEN or settings.LX_AUTH_TOKEN


def _cdp_configured() -> bool:
    """是否具备本地 CDP 刷新条件（配了领星账号密码）"""
    return bool(settings.LX_USERNAME and settings.LX_PASSWORD)


def direct_enabled() -> bool:
    """本地直连兜底是否可用（有静态 token，或配了账号可经 CDP 刷新）"""
    return bool(_direct_token() or _cdp_configured())


def _refresh_direct_token() -> str | None:
    """接入本机 CDP 浏览器登录领星并捕获最新 auth-token，成功后缓存。

    仅当服务站不可用、本地直连鉴权失败时调用；失败返回 None。
    """
    global _direct_token_cache
    if not _cdp_configured():
        logger.warning("本地直连刷新跳过：.env 未配置 LX_USERNAME / LX_PASSWORD")
        return None
    with _direct_lock:
        try:
            from browser_api.lingxing_auth import LingxingAuth

            auth = LingxingAuth(cdp_port=settings.LX_CDP_PORT, cdp_host=settings.LX_CDP_HOST)
            token = auth.ensure_token()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "本地 CDP 刷新领星登录态失败（需先启动本机浏览器: "
                "python -m browser_api.browser_starter --port %s）: %s",
                settings.LX_CDP_PORT, e,
            )
            return None
        if token:
            _direct_token_cache = token
            logger.info("本地 CDP 已刷新领星 auth-token")
        return token


def _direct_headers() -> dict:
    # 与 scraper/lingxing_product_scraper.py 的可用请求头保持一致（origin/referer 固定 huizhixin）
    return {
        "accept": "application/json, text/plain, */*",
        "ak-client-type": "web",
        "ak-origin": "https://huizhixin.lingxing.com",
        "auth-token": _direct_token(),
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://huizhixin.lingxing.com",
        "referer": "https://huizhixin.lingxing.com/",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
        "x-ak-company-id": settings.LX_HEADER_COMPANY_ID or _DEFAULT_COMPANY_ID,
        "x-ak-env-key": settings.LX_HEADER_ENV_KEY or _DEFAULT_ENV_KEY,
        "x-ak-language": "zh",
        "x-ak-platform": "1",
        "x-ak-request-source": "erp",
        "x-ak-uid": settings.LX_HEADER_UID or _DEFAULT_UID,
        "x-ak-version": _X_AK_VERSION,
        "x-ak-zid": "1",
    }


def _direct_post(url: str, body: dict | None, timeout: float) -> dict:
    resp = _session.post(url, headers=_direct_headers(), json=body or {}, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        raise StationError(f"本地直连返回非 JSON: {resp.text[:300]}")


def _extract_items(raw: dict) -> tuple[list, int | None]:
    """领星网页响应 → (列表, total)；鉴权/业务失败抛 StationError。

    兼容 {code, msg, data:{list, total}} / {code, data:[...]} 等结构。
    """
    if not isinstance(raw, dict):
        raise StationError(f"本地直连响应异常: {str(raw)[:200]}")
    code = raw.get("code")
    if code not in _OK_CODES:
        msg = str(raw.get("msg") or "")
        if code in _AUTH_FAIL_CODES or "鉴权失败" in msg or "-999" in msg:
            raise _DirectAuthError(f"本地直连鉴权失败: code={code} msg={msg}")
        raise StationError(f"本地直连业务失败: code={code} msg={msg}")
    data = raw.get("data")
    if data is None:
        items, total = [], raw.get("total")
    elif isinstance(data, list):
        items, total = data, raw.get("total")
    elif isinstance(data, dict):
        items = next((v for k in ("list", "data", "items", "rows", "records")
                      if isinstance(v := data.get(k), list)), [])
        total = data.get("total")
        if total is None:
            total = data.get("count")
    else:
        items, total = [], None
    if total is None:
        total = raw.get("total")
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    return items, total


def _paginate(body: dict) -> tuple[str, int] | None:
    """识别分页方式 → ('offset'|'page', 每页条数)；未分页返回 None"""
    if "offset" in body and "length" in body:
        key, cur = "offset", "length"
    elif "page" in body and "pageSize" in body:
        key, cur = "page", "pageSize"
    else:
        return None
    try:
        size = int(body.get(cur) or 0)
    except (TypeError, ValueError):
        return None
    return (key, size) if size > 0 else None


def _bump_seq(payload: dict) -> None:
    """递增 req_time_sequence 末位序号，避免防重放字段写死导致后续页失败"""
    seq = payload.get("req_time_sequence")
    if not isinstance(seq, str) or "$" not in seq:
        return
    head, _, tail = seq.rpartition("$")
    try:
        payload["req_time_sequence"] = f"{head}${int(tail) + 1}"
    except ValueError:
        pass


def _direct_run(url: str, body: dict | None, pg: tuple[str, int] | None, timeout: float) -> dict:
    """执行一次本地直连抓取（可含翻页）；内部复制 body，避免翻页污染入参。"""
    payload = dict(body or {})
    if pg is None:
        raw = _direct_post(url, payload, timeout)
        items, total = _extract_items(raw)
        return {"success": True, "total": total if total is not None else len(items),
                "pages": 1, "data": items, "message": raw.get("msg") or ""}

    key, size = pg
    all_items: list = []
    total: int | None = None
    pages = 0
    while pages < _MAX_PAGES:
        raw = _direct_post(url, payload, timeout)
        items, tot = _extract_items(raw)
        pages += 1
        all_items.extend(items)
        if tot is not None:
            total = tot
        if not items:
            break
        if total is not None and len(all_items) >= total:
            break
        if len(items) < size:
            break
        if key == "offset":
            payload["offset"] = int(payload.get("offset") or 0) + size
        else:
            payload["page"] = int(payload.get("page") or 1) + 1
        _bump_seq(payload)
    return {"success": True, "total": total if total is not None else len(all_items),
            "pages": pages, "data": all_items, "message": ""}


def direct_fetch(url: str, body: dict | None = None, auto_pagination: bool = False,
                 timeout: float | None = None) -> dict:
    """本地 auth-token 直连领星网页 API，归一化为服务站同款响应。

    鉴权失败（8003/-999 顶号）时经本机 CDP 刷新 auth-token 后自动重试一次。
    返回 {success, total, pages, data:[...], message}，与 station_proxy 对齐。
    """
    if not direct_enabled():
        raise StationError("本地直连兜底未启用（.env 缺少 LX_AUTH_TOKEN，且未配置 CDP 账号）")
    timeout = timeout or settings.LX_STATION_TIMEOUT
    if not _direct_token() and not _refresh_direct_token():
        raise StationError("本地直连兜底无可用 auth-token（CDP 刷新失败）")
    pg = _paginate(body or {}) if auto_pagination else None
    try:
        return _direct_run(url, body, pg, timeout)
    except _DirectAuthError as e:
        logger.warning("本地直连鉴权失败，尝试 CDP 刷新 token 后重试: %s", e)
        if not _refresh_direct_token():
            raise
        return _direct_run(url, body, pg, timeout)


# ──────────────────────────── 统一入口 ────────────────────────────

def station_proxy(api_name: str | None = None, url: str | None = None,
                  body: dict | None = None, account: str | None = None,
                  auto_pagination: bool = False, timeout: float | None = None) -> dict:
    """优先经服务站转发；服务站未配置/失败时回退本地 auth-token 直连。

    返回 {success,total,pages,data,message}；两条通道均不可用时抛 StationError。
    """
    if not api_name and not url:
        raise StationError("station_proxy 需提供 api_name 或 url")
    timeout = timeout or settings.LX_STATION_TIMEOUT

    station_resp: dict | None = None
    station_err: Exception | None = None
    if station_enabled():
        try:
            station_resp = _station_call(api_name, url, body, account, auto_pagination, timeout)
        except StationError as e:
            station_err = e
            logger.warning("领星服务站调用失败，尝试本地直连兜底: %s", e)
    else:
        station_err = StationError(
            "未配置领星 API 服务站，请在 .env 设置 "
            "LX_STATION_BASE_URL / LX_STATION_USER_ID / LX_STATION_PASSWORD"
        )
        logger.warning("%s，尝试本地直连兜底", station_err)

    if isinstance(station_resp, dict) and station_resp.get("success"):
        return station_resp

    # 服务站失败/未配置 → 本地直连兜底（需 url）
    if url and direct_enabled():
        try:
            logger.info("本地 auth-token 直连: %s", url)
            return direct_fetch(url, body, auto_pagination, timeout)
        except Exception as e:  # noqa: BLE001
            if isinstance(station_resp, dict):
                return station_resp  # 保留服务站原始失败响应，便于上层读取 message
            raise StationError(f"{station_err}；本地直连兜底失败: {e}") from e

    if isinstance(station_resp, dict):
        return station_resp
    raise station_err


def station_data(api_name: str | None = None, url: str | None = None,
                 body: dict | None = None, account: str | None = None,
                 auto_pagination: bool = False, timeout: float | None = None) -> list:
    """station_proxy 的便捷封装：校验 success 并返回归一化后的 data 列表。"""
    r = station_proxy(api_name=api_name, url=url, body=body, account=account,
                      auto_pagination=auto_pagination, timeout=timeout)
    if not isinstance(r, dict) or not r.get("success"):
        raise StationError(f"领星数据通道调用失败: {str(r)[:500]}")
    data = r.get("data")
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]
