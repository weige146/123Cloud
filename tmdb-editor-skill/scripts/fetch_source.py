#!/usr/bin/env python3
"""国内影视数据源取数 CLI（tmdb-helper.user.js 取数层的 Python 移植）。

各平台拿分集/详情，产出统一 JSON，供 TMDB 录入前核对与批量提交使用。
依赖：httpx（必需）、beautifulsoup4（仅百度百科子命令）。

子命令：
  episodes URL [--out FILE] [--tsv FILE]      自动识别平台抓整季分集（bilibili/iqiyi/mgtv/qq/youku/hongguo）
  bilibili-search 关键词 [--limit N]          B站番剧搜索（media_bangumi）
  hongguo-search 关键词                       红果短剧站内搜索
  douban-suggest 关键词                       豆瓣搜索建议（subject_suggest）
  douban-detail 豆瓣ID                        豆瓣条目详情（rexxar 主路 + 桌面页补空）
  douban-imdb tt1234567                       IMDb 编号反查豆瓣条目
  baike-search 关键词                         百度百科搜索
  baike-detail 词条URL                        百科词条详情（信息栏 + 海报）
  baike-episodes 词条URL                      百科分集剧情表格
  image 图片URL -o OUT                        按图床 Referer 表下载图片（防盗链）

输出统一 UTF-8 JSON（ensure_ascii=False），--out 落盘否则打 stdout。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from urllib.parse import quote
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:  # pragma: no cover
    print("缺少依赖 httpx：请用 skill 的 .venv/bin/python 运行，或 pip install httpx", file=sys.stderr)
    raise

SITE_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
DOUBAN_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ACCEPT_JSON = "application/json,text/plain,*/*"
ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

DOUBAN_HEADERS = {"User-Agent": DOUBAN_UA, "Accept": ACCEPT_JSON, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://movie.douban.com/"}
# m.douban.com rexxar 接口：Referer 必须是 m 站，否则 400
REXXAR_HEADERS = {"User-Agent": DOUBAN_UA, "Accept": ACCEPT_JSON, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://m.douban.com/"}
BAIKE_HEADERS = {"User-Agent": DOUBAN_UA, "Accept": ACCEPT_HTML, "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://baike.baidu.com/"}

# 被风控时豆瓣把请求 302 到安全验证页，HTTP 仍是 200，只能靠最终 URL 识别
DOUBAN_BLOCKED_RE = re.compile(r"sec\.douban\.com|/accounts/login|/secsdk/")

CLIENT = httpx.Client(follow_redirects=True, timeout=25.0, headers={"User-Agent": SITE_UA, "Accept": ACCEPT_JSON})
_douban_last_at = 0.0


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 25.0, retries: int = 2) -> httpx.Response:
    """带传输层重试的 GET；非 2xx 抛 RuntimeError（由各平台自行决定是否兜底）。"""
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            response = CLIENT.get(url, headers=headers, timeout=timeout)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} {url}")
            return response
        except RuntimeError:
            raise
        except httpx.HTTPError as err:  # 网络层错误才重试
            last_err = err
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"网络请求失败：{last_err}")


def unwrap_jsonp(text: str) -> str:
    text = re.sub(r"^\s*QZOutputJson=", "", text or "")
    return re.sub(r";\s*$", "", text).strip()


def epoch_to_local_date(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return _dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d")


def _pad2(value: int) -> str:
    return f"{value:02d}"


def normalize_air_date(value: Any) -> str:
    """各类日期文本 → YYYY-MM-DD；解析不出返回空串（TMDB 接受空日期，绝不瞎填）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        match = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
        if not match:
            return ""
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if month < 1 or month > 12 or day < 1 or day > 31:
        return ""
    try:
        _dt.date(year, month, day)
    except ValueError:
        return ""
    return f"{year}-{_pad2(month)}-{_pad2(day)}"


def collapse_summary(text: Any) -> str:
    return "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip()).strip()


def looks_latin(value: Any) -> bool:
    text = str(value or "")
    return len(text) >= 2 and re.search(r"[A-Za-z]", text) is not None and re.search(r"[\u4e00-\u9fff]", text) is None


def split_aliases(value: Any) -> List[str]:
    return [part.strip() for part in re.split(r"[/、,，;；]", str(value or "")) if part.strip()]


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# —— 图床防盗链 Referer 表（缺 Referer 会 403 或拿到降级小图） ——
def image_referer(url: str) -> str:
    text = str(url or "")
    if re.search(r"hdslb\.com", text, re.I):
        return "https://www.bilibili.com/"
    if re.search(r"iqiyipic\.com|iqiyi\.com", text, re.I):
        return "https://www.iqiyi.com/"
    if re.search(r"qpic\.cn", text, re.I):
        return "https://v.qq.com/"
    if re.search(r"hitv\.com", text, re.I):
        return "https://www.mgtv.com/"
    if re.search(r"ykimg\.com", text, re.I):
        return "https://v.youku.com/"
    if re.search(r"doubanio\.com|douban\.com", text, re.I):
        return "https://movie.douban.com/"
    if re.search(r"bkimg\.cdn\.bcebos\.com|bkimg", text, re.I):
        return "https://baike.baidu.com/"
    return ""


def upgrade_douban_poster_url(url: str) -> str:
    """豆瓣缩略变体只有 ~480px 宽，过不了 TMDB 最低分辨率；raw 路径取原图。"""
    text = str(url or "").strip()
    if not re.search(r"doubanio\.com|douban\.com", text, re.I):
        return text
    return re.sub(r"(/view/photo/)(?!raw/)[^/]+(/public/)", r"\1raw\2", text, flags=re.I)


def upgrade_baike_poster_url(url: str) -> str:
    """bkimg CDN 的缩放参数把图压到几百像素，去 query 即原图。"""
    text = str(url or "").strip()
    if not re.search(r"bkimg\.cdn\.bcebos\.com|bkimg", text, re.I):
        return text
    return text.split("?")[0]


def upgrade_image_url(url: str) -> str:
    return upgrade_douban_poster_url(upgrade_baike_poster_url(url))


# —— URL 解析（与 tmdb-helper parse*Url 同源） ——
def parse_bilibili_url(url: str) -> Optional[Dict[str, str]]:
    match = re.search(r"bilibili\.com/bangumi/(?:play/(ss|ep)(\d+)|media/md(\d+))", str(url or ""), re.I)
    if not match:
        return None
    if match.group(1):
        return {"type": match.group(1).lower(), "id": match.group(2)}
    return {"type": "md", "id": match.group(3)}


def extract_iqiyi_album_id(url: str, html: str) -> str:
    text = str(html or "")
    if re.search(r"iqiyi\.com/lib/m_", url, re.I):
        match = re.search(r'movlibalbumaid="(\d+)"', text, re.I)
    elif re.search(r"iqiyi\.com/a_", url, re.I):
        match = re.search(r'data-album-id="(\d+)"', text, re.I)
    else:
        match = re.search(r'"albumId":\s*"?(\d+)', text) or re.search(r'"albumId":(\d+),"channelId', text)
    return match.group(1) if match else ""


def parse_mgtv_collection_id(url: str) -> str:
    path = str(url or "").split(".html", 1)[0]
    match = re.search(r"(\d+)", path)
    return match.group(1) if match else ""


def parse_qq_cover_cid(url: str) -> str:
    match = re.search(r"v\.qq\.com/x/cover/([^/.]+)", str(url or ""), re.I)
    return match.group(1) if match else ""


def parse_youku_target(url: str) -> Optional[Dict[str, str]]:
    text = str(url or "")
    show = re.search(r"[?&]s=([a-zA-Z0-9_-]+)", text)
    if show:
        return {"type": "show", "id": show.group(1)}
    vid = re.search(r"[?&]vid=([a-zA-Z0-9_-]+)", text)
    id_path = re.search(r"id_([a-zA-Z0-9_=]+)\.html", text)
    video_id = (vid.group(1) if vid else "") or (id_path.group(1).rstrip("=") if id_path else "")
    return {"type": "video", "id": video_id} if video_id else None


# —— 分集映射（纯函数，输出与粘贴解析同构的表格行） ——
def map_bilibili_episodes(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    eps = result.get("episodes") if isinstance(result, dict) else None
    eps = eps if isinstance(eps, list) else []
    rows: List[Dict[str, Any]] = []
    for ep in eps:
        if not isinstance(ep, dict) or ep.get("badge") == "预告":  # 预告不算正片
            continue
        seq = len(rows) + 1  # 剔除预告后集号必须连续（TMDB 集号要求）
        title = str(ep.get("title") or "")
        long_title = str(ep.get("long_title") or "")
        if ("（上" in title or "（下" in title) and long_title:
            name = f"{title} {long_title}".strip()
        else:
            name = (long_title or title).strip() or f"第 {seq} 集"
        rows.append({
            "episodeNumber": seq,
            "name": name,
            "airDate": epoch_to_local_date(ep.get("pub_time")) if ep.get("pub_time") else normalize_air_date(ep.get("release_date") or ""),
            "runtime": round(float(ep.get("duration") or 0) / 60000),
            "overview": "",
            "stillUrl": str(ep.get("cover") or ""),
        })
    return rows


def upgrade_iqiyi_image_url(url: str, sizes: Any) -> str:
    base = str(url or "").strip()
    if not base or not isinstance(sizes, list):
        return base
    best, best_area = "", 0
    for item in sizes:
        match = re.match(r"^(\d+)_(\d+)$", str(item or ""))
        if not match:
            continue
        area = int(match.group(1)) * int(match.group(2))
        if area > best_area:
            best_area, best = area, f"{match.group(1)}_{match.group(2)}"
    if not best:
        return base
    return f"{re.sub(r'\.[^.]+$', '', base)}_{best}.jpg"


def map_iqiyi_episodes(epsodelist: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ep in epsodelist if isinstance(epsodelist, list) else []:
        if not isinstance(ep, dict):
            continue
        parts = str(ep.get("duration") or "").split(":")
        runtime = 0
        if len(parts) == 3:
            runtime = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 2:
            runtime = int(parts[0] or 0)
        rows.append({
            "episodeNumber": int(ep.get("order") or 0),
            "name": str(ep.get("subtitle") or ep.get("name") or "").strip(),
            "airDate": normalize_air_date(ep.get("period") or ""),
            "runtime": runtime,
            "overview": re.sub(r"\s*\n\s*", " ", str(ep.get("description") or "")).strip(),
            "stillUrl": upgrade_iqiyi_image_url(str(ep.get("imageUrl") or ""), ep.get("imageSize")),
        })
    return [row for row in rows if row["episodeNumber"] > 0]


def append_mgtv_oss_resize(url: str) -> str:
    """芒果图床是阿里云 OSS：裸 URL 只有 860×484 预览档，带 resize 参数才有 ≥1280 原图。"""
    text = str(url or "").strip()
    if not text or not re.search(r"hitv\.com", text, re.I):
        return text
    return f"{text}{'&' if '?' in text else '?'}x-oss-process=image/resize,w_1280"


def map_mgtv_episodes(list_data: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ep in list_data if isinstance(list_data, list) else []:
        if not isinstance(ep, dict) or str(ep.get("isIntact")) != "1":
            continue
        img = re.sub(r"_[^_]*$", "", str(ep.get("img") or ""))
        rows.append({
            "episodeNumber": int(ep.get("t1") or 0),
            "name": str(ep.get("t2") or "").strip(),
            "airDate": normalize_air_date(str(ep.get("ts") or "").split(" ")[0]),
            "runtime": int(str(ep.get("time") or "").split(":")[0] or 0),
            "overview": "",
            "stillUrl": append_mgtv_oss_resize(img),
        })
    return [row for row in rows if row["episodeNumber"] > 0]


def clean_qq_title(title: str) -> str:
    return re.sub(r"^第\d+[集期]\s*", "", re.sub(r"_\d+$", "", str(title or ""))).strip()


def map_qq_union_episodes(fields_list: Any) -> List[Dict[str, Any]]:
    items = fields_list if isinstance(fields_list, list) else []
    counter = 1
    rows: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = item.get("category_map")
        tags = category if isinstance(category, list) else []
        if not any(isinstance(tag, str) and "正片" in tag for tag in tags):
            continue
        number = 0
        try:
            number = int(str(item.get("episode") or ""))
        except ValueError:
            number = 0
        if not number:
            number = counter
        if number >= counter:
            counter = number + 1
        rows.append({
            "episodeNumber": number,
            "name": clean_qq_title(str(item.get("second_title") or item.get("title") or "")),
            "airDate": normalize_air_date(str(item.get("video_checkup_time") or "").split(" ")[0]),
            "runtime": round(float(item.get("duration") or 0) / 60),
            "overview": re.sub(r"\s*\n\s*", " ", str(item.get("desc") or "")).strip(),
            "stillUrl": str(item.get("pic160x90") or "").replace("/160", "/1280"),
        })
    rows.sort(key=lambda row: row["episodeNumber"])
    return rows


def map_youku_videos(videos: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, ep in enumerate(videos if isinstance(videos, list) else []):
        if not isinstance(ep, dict):
            continue
        try:
            seq = int(str(ep.get("seq") or ""))
        except ValueError:
            seq = 0
        rows.append({
            "episodeNumber": seq or index + 1,  # seq 是平台正片集号，缺失时顺序回退
            "name": str(ep.get("rc_title") or ep.get("title") or "").strip(),
            "airDate": normalize_air_date(str(ep.get("published") or "").split(" ")[0]),
            "runtime": round(float(ep.get("duration") or 0) / 60),
            "overview": re.sub(r"\s*\n\s*", " ", str(ep.get("description") or "")).strip(),
            "stillUrl": str(ep.get("bigthumbnail") or ep.get("bigThumbnail") or ep.get("thumbnail") or ""),
        })
    return rows


def extract_hongguo_router_data(html: str) -> Dict[str, Any]:
    """红果 SSR 页内嵌 window._ROUTER_DATA：括号配对截出完整 JSON（跳过字符串内的花括号/引号）。"""
    text = str(html or "")
    marker = text.find("_ROUTER_DATA")
    if marker < 0:
        raise RuntimeError("页面里没有 _ROUTER_DATA（页面结构可能已变化）")
    start = text.find("{", marker)
    depth, in_str, esc, i = 0, False, False, start
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return json.loads(text[start:i + 1])


def parse_hongguo_series(html: str) -> Dict[str, Any]:
    data = extract_hongguo_router_data(html)
    detail = (data.get("loaderData") or {}).get("detail_page") or {}
    detail = detail.get("seriesDetail") or {}
    if not detail:
        raise RuntimeError("页面数据里没有剧集详情")
    vids = detail.get("vid_list") if isinstance(detail.get("vid_list"), list) else []
    total = int(detail.get("episode_cnt") or 0) or len(vids)
    episodes = [
        {"episodeNumber": index + 1, "name": "", "airDate": "", "runtime": 0, "overview": "", "stillUrl": ""}
        for index in range(min(len(vids), 500))
    ]
    return {
        "title": str(detail.get("series_name") or "").strip(),
        "overview": str(detail.get("series_intro") or "").strip(),
        "cover": str(detail.get("series_cover") or ""),
        "episodeCnt": total,
        "episodes": episodes,
    }


def map_hongguo_search_list(search_list: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in search_list if isinstance(search_list, list) else []:
        if not isinstance(item, dict):
            continue
        video = item.get("video_data")
        if not isinstance(video, dict) or not video.get("series_id"):
            continue
        rows.append({
            "platform": "hongguo",
            "platformName": "红果短剧",
            "id": str(video.get("series_id")),
            "title": str(video.get("series_title") or item.get("name") or "").strip(),
            "episodeCnt": int(video.get("episode_cnt") or 0),
            "intro": str(video.get("series_intro") or "").strip(),
            "cover": str(video.get("series_cover") or ""),
        })
    return rows


def map_bilibili_search_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = ((data or {}).get("data") or {}).get("result")
    result = result if isinstance(result, list) else []
    rows: List[Dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        sid = int(item.get("season_id") or 0)
        if not sid:
            continue
        eps = item.get("eps")
        rows.append({
            "platform": "bilibili",
            "platformName": "哔哩哔哩",
            "id": str(sid),
            "title": re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip(),
            "episodeCnt": len(eps) if isinstance(eps, list) else 0,
            "intro": re.sub(r"<[^>]+>", "", str(item.get("desc") or "")).strip(),
            "cover": str(item.get("cover") or ""),
        })
    return rows


def site_get_json(url: str, jsonp: bool = False, headers: Optional[Dict[str, str]] = None) -> Any:
    response = http_get(url, headers=headers or {"User-Agent": SITE_UA, "Accept": ACCEPT_JSON})
    text = unwrap_jsonp(response.text) if jsonp else response.text
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"接口响应不是 JSON（可能被风控或需要登录）：{url}") from err


def site_get_text(url: str, user_agent: Optional[str] = None, headers: Optional[Dict[str, str]] = None) -> str:
    merged = {"User-Agent": user_agent or SITE_UA, "Accept": ACCEPT_HTML}
    merged.update(headers or {})
    return http_get(url, headers=merged, timeout=30.0).text


def bili_search_json(keyword: str) -> Dict[str, Any]:
    """B站搜索需要 buvid cookie：缺失时接口返回风控页，先访问一次主站引导 cookie 再重试。"""
    url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=media_bangumi&keyword={quote(keyword)}&page=1"
    data: Optional[Dict[str, Any]] = None
    try:
        data = site_get_json(url)
    except RuntimeError:
        data = None
    if not isinstance(data, dict) or data.get("code") != 0:
        site_get_text("https://www.bilibili.com/")
        data = site_get_json(url)
    if not isinstance(data, dict) or data.get("code") != 0:
        raise RuntimeError(f"B站搜索接口返回异常（code={data.get('code') if isinstance(data, dict) else '?'}）")
    return data


# —— 六平台整季抓取 ——
def fetch_bilibili(url: str) -> Dict[str, Any]:
    target = parse_bilibili_url(url)
    if not target:
        raise RuntimeError("不是可识别的 B站番剧链接（需要 ss/ep/md）")
    season_id = target["id"]
    if target["type"] == "md":
        media = site_get_json(f"https://api.bilibili.com/pgc/review/user?media_id={target['id']}")
        season_id = str(((media.get("result") or {}).get("media") or {}).get("season_id") or "")
        if not season_id:
            raise RuntimeError("B站没找到该媒体对应的季")
    elif target["type"] == "ep":
        by_ep = site_get_json(f"https://api.bilibili.com/pgc/view/web/season?ep_id={target['id']}")
        result = by_ep.get("result") if by_ep.get("code") == 0 else None
        if not result:
            raise RuntimeError("B站没找到该分集对应的季")
        season_id = str(result.get("season_id") or target["id"])
    data = site_get_json(f"https://api.bilibili.com/pgc/view/web/season?season_id={season_id}")
    if not (isinstance(data, dict) and data.get("code") == 0 and data.get("result")):
        raise RuntimeError("B站接口没有返回该季数据")
    result = data["result"]
    return {"title": str(result.get("title") or ""), "episodes": map_bilibili_episodes(result)}


def fetch_iqiyi(url: str) -> Dict[str, Any]:
    # 单集页（v_…）桌面版是 JS 空壳：改抓移动端 SSR 页（去查询参数），albumId 埋在选集数据里
    page_url = url
    if re.search(r"iqiyi\.com/v_[a-z0-9]+\.html", url, re.I):
        page_url = re.match(r"https?://[^?]+", url, re.I).group(0).replace("//www.iqiyi.com", "//m.iqiyi.com")
    html = site_get_text(page_url, user_agent=MOBILE_UA)
    album_id = extract_iqiyi_album_id(url, html)
    if not album_id:
        raise RuntimeError("爱奇艺页面里没找到专辑 ID（可能需要登录或页面已变化）")
    title, overview, cover = "", "", ""
    try:
        base = site_get_json(f"https://pcw-api.iqiyi.com/album/album/baseinfo/{album_id}")
        data = base.get("data") or {}
        title, overview, cover = str(data.get("name") or ""), str(data.get("description") or ""), str(data.get("imageUrl") or "")
    except RuntimeError:
        pass  # 专辑信息拿不到不影响分集
    episodes: List[Dict[str, Any]] = []
    for page in range(1, 21):
        data = site_get_json(f"https://pcw-api.iqiyi.com/albums/album/avlistinfo?aid={album_id}&page={page}&size=100")
        epsodelist = (data.get("data") or {}).get("epsodelist")
        epsodelist = epsodelist if isinstance(epsodelist, list) else []
        if not epsodelist:
            break
        episodes.extend(map_iqiyi_episodes(epsodelist))
        if len(epsodelist) < 100:
            break
    return {"title": title, "overview": overview, "cover": cover, "episodes": episodes}


def fetch_mgtv(url: str) -> Dict[str, Any]:
    collection_id = parse_mgtv_collection_id(url)
    if not collection_id:
        raise RuntimeError("芒果TV链接里没找到专辑 ID")
    episodes: List[Dict[str, Any]] = []
    for page in range(1, 21):
        data = site_get_json(f"https://pcweb.api.mgtv.com/episode/list?_support=10000000&version=5.5.35&collection_id={collection_id}&page={page}&size=50")
        payload = data.get("data") if isinstance(data, dict) else None
        if not payload:
            raise RuntimeError("芒果TV接口没有返回数据")
        episodes.extend(map_mgtv_episodes(payload.get("list")))
        total_page = int(payload.get("total_page") or 0)
        current = int(payload.get("current_page") or page)
        if not total_page or current >= total_page:
            break
    return {"title": "", "episodes": episodes}


def fetch_qq(url: str) -> Dict[str, Any]:
    cid = parse_qq_cover_cid(url)
    if not cid:
        raise RuntimeError("腾讯视频链接里没找到 cid（需要 v.qq.com/x/cover/… 页面链接）")
    index = site_get_json(
        f"https://data.video.qq.com/fcgi-bin/data?otype=json&tid=431&idlist={quote(cid)}&appid=10001005&appkey=0d1a9ddd94de871b",
        jsonp=True,
    )
    results = index.get("results") if isinstance(index, dict) else None
    fields = (results[0].get("fields") if isinstance(results, list) and results and isinstance(results[0], dict) else None) or {}
    video_ids = fields.get("video_ids")
    if not isinstance(video_ids, list) or not video_ids:
        raise RuntimeError("腾讯视频没返回分集列表")
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(video_ids), 30):
        idlist = ",".join(str(vid) for vid in video_ids[start:start + 30])
        data = site_get_json(
            f"https://union.video.qq.com/fcgi-bin/data?otype=json&tid=682&appid=20001238&appkey=6c03bbe9658448a4&idlist={idlist}",
            jsonp=True,
        )
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, list):
            rows.extend(map_qq_union_episodes([item.get("fields") for item in results if isinstance(item, dict)]))
    return {"title": "", "episodes": rows}


def fetch_hongguo(url: str) -> Dict[str, Any]:
    match = re.search(r"player/(\d+)", str(url)) or re.search(r"series_id=(\d+)", str(url))
    if not match:
        raise RuntimeError("红果链接里没找到 series_id（需要 /player/767… 或 ?series_id=767… 形式）")
    html = site_get_text(f"https://hongguoduanju.com/detail?series_id={match.group(1)}")
    result = parse_hongguo_series(html)
    if not result["episodes"]:
        raise RuntimeError(f"红果没返回《{result['title'] or '该剧'}》的分集列表")
    return {"title": result["title"], "episodes": result["episodes"]}


def fetch_youku(url: str) -> Dict[str, Any]:
    target = parse_youku_target(url)
    if not target:
        raise RuntimeError("优酷链接里没找到 show/video ID")
    show_id = target["id"] if target["type"] == "show" else ""
    if not show_id:
        video = site_get_json(
            f"https://openapi.youku.com/v2/videos/show.json?video_id={target['id']}&ext=show&client_id=0dec1b5a3cb570c1&package=com.huawei.hwvplayer.youku"
        )
        if video.get("error"):
            raise RuntimeError("优酷没找到该视频（链接可能已失效）")
        show_id = str((video.get("show") or {}).get("id") or target["id"])
    episodes: List[Dict[str, Any]] = []
    for page in range(1, 21):
        data = site_get_json(
            f"https://openapi.youku.com/v2/shows/videos.json?show_id={show_id}&show_videotype={quote('正片')}"
            f"&page={page}&count=30&client_id=0dec1b5a3cb570c1&package=com.huawei.hwvplayer.youku"
        )
        if data.get("error"):
            raise RuntimeError("优酷开放接口返回错误（接口老旧，可能已停用）")
        videos = data.get("videos") if isinstance(data.get("videos"), list) else []
        episodes.extend(map_youku_videos(videos))
        if not videos or not data.get("total") or page * 30 >= int(data.get("total") or 0):
            break
    return {"title": "", "episodes": episodes}


SITE_SOURCES: List[Tuple[Callable[[str], bool], Callable[[str], Dict[str, Any]], str]] = [
    (lambda url: bool(parse_bilibili_url(url)), fetch_bilibili, "bilibili"),
    (lambda url: bool(re.search(r"iqiyi\.com/(a_|v_|lib/m_)", url, re.I)), fetch_iqiyi, "iqiyi"),
    (lambda url: bool(re.search(r"mgtv\.com", url, re.I)), fetch_mgtv, "mgtv"),
    (lambda url: bool(parse_qq_cover_cid(url)), fetch_qq, "qq"),
    (lambda url: bool(re.search(r"hongguoduanju\.com", url, re.I)), fetch_hongguo, "hongguo"),
    (lambda url: bool(parse_youku_target(url)), fetch_youku, "youku"),
]


def match_site_source(url: str) -> Optional[Tuple[Callable[[str], Dict[str, Any]], str]]:
    text = str(url or "").strip()
    if not re.match(r"^https?://", text, re.I):
        return None
    for matcher, fetcher, name in SITE_SOURCES:
        try:
            if matcher(text):
                return fetcher, name
        except Exception:
            continue
    return None


# —— 豆瓣 ——
def _douban_throttle(min_interval_ms: int = 2000) -> None:
    global _douban_last_at
    elapsed = time.monotonic() - _douban_last_at
    if elapsed * 1000 < min_interval_ms:
        time.sleep(min_interval_ms / 1000 - elapsed)
    _douban_last_at = time.monotonic()


def douban_fetch(url: str, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    _douban_throttle()
    response = http_get(url, headers=headers or DOUBAN_HEADERS)
    if DOUBAN_BLOCKED_RE.search(str(response.url)):
        raise RuntimeError("请求被豆瓣重定向到安全验证页（风控），先在浏览器打开一次豆瓣完成验证再试")
    return response


def douban_suggest(keyword: str) -> List[Dict[str, Any]]:
    keyword = keyword.strip()
    if not keyword:
        return []
    data = douban_fetch(f"https://movie.douban.com/j/subject_suggest?q={quote(keyword)}").json()
    media_label = {"movie": "电影", "tv": "剧集"}
    rows: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not re.match(r"^\d+$", str(item.get("id") or "")):
            continue
        media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
        rows.append({
            "id": str(item.get("id")),
            "title": str(item.get("title") or "").strip(),
            "subTitle": str(item.get("sub_title") or "").strip(),
            "year": str(item.get("year") or "").strip(),
            "mediaType": media_label.get(media_type, media_type),
            "url": str(item.get("url") or "").strip(),
        })
    return rows


def douban_by_imdb(imdb_id: str) -> Optional[Dict[str, Any]]:
    imdb_id = imdb_id.strip()
    if not re.match(r"^tt\d+$", imdb_id):
        return None
    response = douban_fetch(f"https://www.douban.com/search?cat=1002&q={imdb_id}")
    match = re.search(r"movie\.douban\.com(?:%2F|/)subject(?:%2F|/)(\d+)", response.text, re.I)
    return {"id": match.group(1)} if match else None


def parse_douban_rexxar(data: Dict[str, Any], douban_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    if not title:
        return None

    def join_list(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        parts = [str(item.get("name") if isinstance(item, dict) else item or "").strip() for item in value]
        return "/".join(part for part in parts if part)

    runtime = 0
    for item in data.get("durations") if isinstance(data.get("durations"), list) else []:
        match = re.search(r"(\d{1,4})\s*分钟", str(item or ""))
        if match:
            runtime = int(match.group(1))
    date = ""
    for item in data.get("pubdate") if isinstance(data.get("pubdate"), list) else []:
        date = normalize_air_date(str(item or "").split("(")[0].split("（")[0])
        if date:
            break
    aliases = [str(item or "").strip() for item in (data.get("aka") if isinstance(data.get("aka"), list) else []) if str(item or "").strip()]
    original_title = str(data.get("original_title") or "").strip() or next((alias for alias in aliases if looks_latin(alias)), "")
    rating = data.get("rating") if isinstance(data.get("rating"), dict) else {}
    pic = data.get("pic") if isinstance(data.get("pic"), dict) else {}
    rating_value = float(rating.get("value") or 0)
    return {
        "doubanId": str(douban_id or data.get("id") or ""),
        "title": title,
        "year": re.search(r"(1[89]\d{2}|20\d{2})", str(data.get("year") or "")).group(1) if re.search(r"(1[89]\d{2}|20\d{2})", str(data.get("year") or "")) else "",
        "date": date,
        "overview": collapse_summary(str(data.get("intro") or "")),
        "runtime": runtime,
        "originalTitle": original_title,
        "genres": join_list(data.get("genres")),
        "countries": join_list(data.get("countries")),
        "languages": join_list(data.get("languages")),
        "aliases": "/".join(aliases),
        "rating": f"{rating_value:.1f}" if rating_value > 0 else "",
        "episodeCount": int(data.get("episodes_count") or 0),
        "directors": join_list(data.get("directors")),
        "writers": join_list(data.get("writers")),
        "cast": join_list(data.get("actors")),
        "poster": upgrade_douban_poster_url(str(pic.get("large") or pic.get("normal") or data.get("image") or data.get("cover_url") or "").strip()),
        "doubanUrl": f"https://movie.douban.com/subject/{douban_id}/",
    }


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")


def parse_douban_desktop_html(html: str, douban_id: str) -> Dict[str, Any]:
    """豆瓣桌面页正则版解析（rexxar 缺字段时补全用；登录态完整 HTML 才有全部字段）。"""
    text = str(html or "")

    def grab(pattern: str) -> str:
        match = re.search(pattern, text, re.S)
        return match.group(1).strip() if match else ""

    title = grab(r'<span[^>]*property="v:itemreviewed"[^>]*>([^<]+)</span>')
    year_match = re.search(r'#content h1 \.year|"year">\((\d{4})\)<', text) or re.search(r'<span class="year">\((\d{4})\)</span>', text)
    summary = " ".join(re.findall(r'<span[^>]*property="v:summary"[^>]*>(.*?)</span>', text, re.S))
    rating = grab(r'<strong[^>]*property="v:average"[^>]*>([^<]+)</strong>')
    poster = grab(r'id="mainpic"[^>]*<img[^>]*src="([^"]+)"') or grab(r'property="og:image"\s+content="([^"]+)"')
    info_html = grab(r'<div id="info">(.*?)</div>')
    info_text = _strip_tags(info_html)

    def info_value(label: str) -> str:
        match = re.search(rf"{label}\s*[:：]\s*([^\n]+)", info_text)
        return match.group(1).strip() if match else ""

    runtime = 0
    runtime_match = re.search(r"单集片长\s*[:：]?\s*([^\n]+)", info_text)
    if runtime_match:
        numbers = [int(n) for n in re.findall(r"(\d{1,4})\s*分钟", runtime_match.group(1))]
        if numbers:
            runtime = numbers[-1]
    date = normalize_air_date(info_value("首播") or info_value("上映日期") or info_value("播出日期"))
    aliases = info_value("又名")
    original_title = ""
    for alias in split_aliases(aliases):
        if looks_latin(alias):
            original_title = alias
            break

    def people(label: str, max_chars: int = 400) -> str:
        match = re.search(rf"{label}\s*[:：]\s*([^\n]+)", info_text)
        if not match:
            return ""
        return "/".join(split_aliases(match.group(1)[:max_chars]))

    return {
        "doubanId": str(douban_id or ""),
        "title": _strip_tags(title),
        "year": year_match.group(1) if year_match else "",
        "date": date,
        "overview": collapse_summary(_strip_tags(summary)),
        "runtime": runtime,
        "originalTitle": original_title,
        "genres": info_value("类型"),
        "countries": info_value(r"制片国家/地区") or info_value("制片地区"),
        "languages": info_value("语言"),
        "aliases": aliases,
        "rating": rating,
        "episodeCount": int(info_value("集数")) if re.match(r"^\d{1,4}$", info_value("集数")) else 0,
        "directors": people("导演"),
        "writers": people("编剧"),
        "cast": people("主演", 1200),
        "poster": upgrade_douban_poster_url(poster),
        "doubanUrl": f"https://movie.douban.com/subject/{douban_id}/",
    }


def douban_detail_has_gaps(detail: Dict[str, Any]) -> bool:
    return any(not str(detail.get(key) or "").strip() for key in ["writers", "directors", "genres", "countries", "languages", "aliases", "overview"])


def merge_detail(base: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    """只把 fallback 里的非空字段填进 base 的空位，不覆盖已有值。"""
    merged = dict(base or {})
    for key, incoming in (fallback or {}).items():
        if incoming in (None, "", 0) or (isinstance(incoming, list) and not incoming):
            continue
        if merged.get(key) in (None, "", 0) or (isinstance(merged.get(key), list) and not merged.get(key)):
            merged[key] = incoming
    return merged


def douban_detail(douban_id: str) -> Dict[str, Any]:
    douban_id = re.sub(r"\D", "", str(douban_id or ""))
    if not douban_id:
        raise RuntimeError("无效的豆瓣条目 ID")
    detail: Optional[Dict[str, Any]] = None
    last_error: Optional[Exception] = None
    try:
        # 移动端 rexxar 接口无需登录；剧集 ID 请求 movie 路径时 301 到 tv，httpx 自动跟随
        response = douban_fetch(f"https://m.douban.com/rexxar/api/v2/movie/{douban_id}", REXXAR_HEADERS)
        detail = parse_douban_rexxar(response.json(), douban_id)
    except Exception as err:
        last_error = err
    if detail and douban_detail_has_gaps(detail):
        try:
            html = douban_fetch(f"https://movie.douban.com/subject/{douban_id}/").text
            if "v:itemreviewed" in html:
                html_detail = parse_douban_desktop_html(html, douban_id)
                if html_detail["title"]:
                    detail = merge_detail(detail, html_detail)
        except Exception:
            pass
    if not detail:
        try:
            html = douban_fetch(f"https://movie.douban.com/subject/{douban_id}/").text
            detail = parse_douban_desktop_html(html, douban_id)
        except Exception as err:
            last_error = err
    if not detail or not str(detail.get("title") or "").strip():
        reason = f"：{last_error}" if last_error else ""
        raise RuntimeError(f"豆瓣条目读取失败{reason}。可稍后重试，或先在浏览器登录豆瓣再试")
    return detail


# —— 百度百科（bs4 解析；页面多代结构并存，全部多选择器兜底） ——
def _baike_clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\[[\d\-]+\]", "", str(value or ""))).strip()


def _baike_get(url: str) -> str:
    resp = http_get(url, headers=BAIKE_HEADERS)
    final = str(resp.url)
    if re.search(r"baike\.baidu\.com/(security|verify|robot)", final) or "wappass" in final:
        raise RuntimeError("百度百科被风控（安全验证页），先在浏览器打开一次 baike.baidu.com 完成验证再试")
    return resp.text


def baike_search(keyword: str) -> List[Dict[str, Any]]:
    from bs4 import BeautifulSoup

    html = _baike_get(f"https://baike.baidu.com/search?word={quote(keyword)}")
    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict[str, Any]] = []
    for node in soup.select("div.search-result-item, .result-list .item, dl"):
        link = node.select_one("a[href*='/item/']")
        if not link:
            continue
        href = link.get("href") or ""
        if href.startswith("/"):
            href = f"https://baike.baidu.com{href}"
        title = _baike_clean_text(link.get_text())
        if not title:
            continue
        desc_node = node.select_one(".intro, .abstract, dd .intro")
        rows.append({"title": title, "url": href, "intro": _baike_clean_text(desc_node.get_text()) if desc_node else ""})
    if not rows:
        # 改版兜底：词条名直连
        rows.append({"title": keyword, "url": f"https://baike.baidu.com/item/{quote(keyword)}", "intro": ""})
    return rows


def baike_detail(url: str) -> Dict[str, Any]:
    from bs4 import BeautifulSoup

    html = _baike_get(url)
    soup = BeautifulSoup(html, "html.parser")
    title = _baike_clean_text(soup.select_one("h1").get_text()) if soup.select_one("h1") else ""
    poster = ""
    pic = soup.select_one(".summary-pic img, .main-content img[src*='bkimg']")
    if pic:
        poster = upgrade_baike_poster_url(pic.get("src") or "")
    pairs: Dict[str, str] = {}
    for dt in soup.select("dt, .basic-info .term"):
        dd = dt.find_next_sibling(["dd", ".value"]) if dt.name == "dt" else None
        node = dd if dd is not None else dt.find_next_sibling(class_=re.compile("value"))
        if node is None:
            continue
        pairs[_baike_clean_text(dt.get_text())] = _baike_clean_text(node.get_text())

    def pair(*labels: str) -> str:
        for label in labels:
            if pairs.get(label):
                return pairs[label]
        return ""

    return {
        "title": title,
        "url": url,
        "poster": poster,
        "directors": pair("导演"),
        "writers": pair("编剧"),
        "cast": pair("主演", "主演配音"),
        "genres": pair("类型"),
        "countries": pair("制片地区", "制片国家/地区"),
        "languages": pair("语言"),
        "episodeCount": int(pair("集数")) if re.match(r"^\d{1,4}$", pair("集数")) else 0,
        "premiere": pair("首播", "首播时间", "播出时间"),
        "runtime": pair("每集长度", "单集片长"),
        "producers": pair("出品方", "出品公司"),
        "platforms": pair("播出平台"),
    }


def map_baike_episode_cells(cells: List[str]) -> Optional[Dict[str, Any]]:
    cleaned = [_baike_clean_text(cell) for cell in cells]
    cleaned = [cell for cell in cleaned if cell]
    if not cleaned:
        return None
    match = re.search(r"(?:第\s*)?(\d{1,4})\s*(?:[集期话])?", cleaned[0])
    number = int(match.group(1)) if match else 0
    if not number or number > 2000:
        return None
    episode: Dict[str, Any] = {"episodeNumber": number, "name": "", "airDate": "", "overview": "", "runtime": 0, "stillUrl": ""}
    rest = cleaned[1:]
    date_index = next((i for i, cell in enumerate(rest) if normalize_air_date(cell)), -1)
    if date_index >= 0:
        episode["airDate"] = normalize_air_date(rest[date_index])
        rest.pop(date_index)
    if rest:
        longest_index = max(range(len(rest)), key=lambda i: len(rest[i]))
        longest = rest[longest_index]
        if len(longest) >= 15 or len(rest) > 1:
            episode["overview"] = longest
            rest.pop(longest_index)
            if rest:
                episode["name"] = rest[0]
        else:
            episode["name"] = longest
    return episode


def parse_baike_episodes(html: str) -> Dict[str, Any]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(str(html or ""), "html.parser")

    def is_episode_table(table) -> bool:
        rows = table.select("tr")
        if len(rows) < 2:
            return False
        head = _baike_clean_text(rows[0].get_text())
        return bool(re.search(r"集数|剧情|分集|集名", head))

    tables: List[Any] = []
    seen_ids = set()
    headings = [el for el in soup.select("h2, h3, h4, .para-title, .title-text") if re.search(r"分集剧情|分集介绍|各集剧情|剧集介绍", _baike_clean_text(el.get_text()))]
    for heading in headings:
        node = heading.find_next_sibling()
        hops = 0
        while node is not None and hops < 15:
            for table in node.find_all("table") if hasattr(node, "find_all") else []:
                if id(table) not in seen_ids:
                    seen_ids.add(id(table))
                    tables.append(table)
            if tables:
                break
            if node.name in ("h1", "h2", "h3", "h4"):
                break
            node = node.find_next_sibling()
            hops += 1
    if not tables:
        for table in soup.find_all("table"):
            if id(table) not in seen_ids and is_episode_table(table):
                tables.append(table)
    episodes: List[Dict[str, Any]] = []
    seen_numbers = set()
    for table in tables:
        if not is_episode_table(table):
            continue
        for row in table.select("tr"):
            cells = [cell.get_text() for cell in row.select("td, th")]
            episode = map_baike_episode_cells(cells)
            if episode and episode["episodeNumber"] not in seen_numbers:
                seen_numbers.add(episode["episodeNumber"])
                episodes.append(episode)
    episodes.sort(key=lambda item: item["episodeNumber"])
    return {"title": "", "overview": "", "cover": "", "episodes": episodes}


def baike_episodes(url: str) -> Dict[str, Any]:
    if not re.search(r"baike\.baidu\.com/item/", str(url or ""), re.I):
        raise RuntimeError("没有可抓取的百科词条链接")
    result = parse_baike_episodes(_baike_get(url))
    if not result["episodes"]:
        raise RuntimeError("该词条页没有解析到分集剧情表格（部分剧集的分集剧情在独立词条里，可搜索「剧名 分集剧情」后重试）")
    return result


# —— 图片下载 ——
def download_image(url: str, out_path: str) -> str:
    final_url = upgrade_image_url(url)
    headers = {"User-Agent": SITE_UA, "Accept": "image/avif,image/webp,image/apichrome,image/*,*/*;q=0.8", "Referer": image_referer(final_url) or "https://www.google.com/"}
    response = http_get(final_url, headers=headers, timeout=40.0)
    data = response.content
    if not data:
        raise RuntimeError("图片下载结果为空")
    with open(out_path, "wb") as fh:
        fh.write(data)
    return f"{out_path}  {len(data)} bytes  referer={headers['Referer']}"


def episodes_tsv(episodes: List[Dict[str, Any]]) -> str:
    lines = ["集号\t集名\t日期\t时长\t简介"]

    def clean(value: Any) -> str:
        return str(value or "").replace("\t", " ").replace("\n", " ")

    for ep in episodes:
        lines.append("\t".join([clean(ep.get("episodeNumber")), clean(ep.get("name")), clean(ep.get("airDate")), clean(ep.get("runtime")), clean(ep.get("overview"))]))
    return "\n".join(lines)


# —— 灵活排期引擎（tmdb-helper buildEpisodeSchedule 移植） ——
# 官方 /bible/air-dates：日期是本地时区当日的真实日历日；同一天播多集共用同一日期、集号连续。
WEEKDAY_TOKENS = {"日": 0, "天": 0, "sunday": 0, "sun": 0, "一": 1, "monday": 1, "mon": 1, "二": 2, "tuesday": 2, "tue": 2, "tues": 2,
                  "三": 3, "wednesday": 3, "wed": 3, "四": 4, "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
                  "五": 5, "friday": 5, "fri": 5, "六": 6, "saturday": 6, "sat": 6,
                  "0": 0, "7": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}
WEEKDAY_NAMES = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _clamp_int(value: Any, min_value: int, max_value: int, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(min_value, min(max_value, number))


def parse_weekdays(text: Any) -> List[int]:
    """「周一、周四」/「1,4」/「mon thu」/「周中/周末」→ 排序去重的星期数字（0=周日）。"""
    found: List[int] = []

    def push(day: int) -> None:
        if 0 <= day <= 6 and day not in found:
            found.append(day)

    lower = str(text or "").lower()
    expanded = re.sub(r"周[一二三四五六日天]|星期[一二三四五六日天]|周中|周末", lambda m: _expand_weekday_token(m.group(0), push), lower)
    for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", expanded):
        if token and token in WEEKDAY_TOKENS:
            push(WEEKDAY_TOKENS[token])
    return sorted(found)


def _expand_weekday_token(token: str, push: Callable[[int], None]) -> str:
    if token == "周中":
        for day in (1, 2, 3, 4, 5):
            push(day)
        return " "
    if token == "周末":
        for day in (0, 6):
            push(day)
        return " "
    push(WEEKDAY_TOKENS.get(token[-1], -1))
    return " "


def build_episode_schedule(options: Dict[str, Any]) -> Dict[str, Any]:
    """生成排期分集：weekly 从首播日（含当日）逐日挑更新日；interval 固定间隔推进。

    每个更新日发 perSlot 集（单日多集共用日期、集号连续）。name 模板支持 {n}集号 {i}序号 {k}当日第几集。
    """
    opts = {
        "pattern": "interval" if options.get("pattern") == "interval" else "weekly",
        "startNumber": _clamp_int(options.get("startNumber"), 1, 2000, 1),
        "count": _clamp_int(options.get("count"), 0, 500, 0),
        "perSlot": _clamp_int(options.get("perSlot"), 1, 20, 1),
        "intervalDays": _clamp_int(options.get("intervalDays"), 1, 60, 7),
        "runtime": _clamp_int(options.get("runtime"), 0, 600, 0),
        "weekdays": parse_weekdays(options.get("weekdays")),
        "startDate": normalize_air_date(options.get("startDate")),
        "template": str(options.get("titleTemplate") or "").strip() or "第{n}集",
    }
    if not opts["count"]:
        return {"episodes": [], "error": "请填写要生成的集数", "meta": None}
    if not opts["startDate"]:
        return {"episodes": [], "error": "首播日期无效（示例 2026-01-01）", "meta": None}
    if opts["pattern"] == "weekly" and not opts["weekdays"]:
        return {"episodes": [], "error": "每周排期至少选择一个更新日（如：一、四）", "meta": None}
    slot_count = -(-opts["count"] // opts["perSlot"])
    dates: List[_dt.date] = []
    if opts["pattern"] == "weekly":
        cursor = _dt.date.fromisoformat(opts["startDate"])
        guard = 0
        while len(dates) < slot_count and guard < 4000:
            if cursor.weekday() in [(day - 1) % 7 for day in opts["weekdays"]]:
                dates.append(cursor)
            cursor += _dt.timedelta(days=1)
            guard += 1
    else:
        cursor = _dt.date.fromisoformat(opts["startDate"])
        for i in range(slot_count):
            dates.append(cursor + _dt.timedelta(days=i * opts["intervalDays"]))
    if len(dates) < slot_count:
        return {"episodes": [], "error": "排期推进异常（日期范围过大），请检查更新日设置", "meta": None}
    episodes: List[Dict[str, Any]] = []
    number = opts["startNumber"]
    for date in dates:
        iso = date.isoformat()
        for k in range(opts["perSlot"]):
            if len(episodes) >= opts["count"]:
                break
            name = opts["template"].replace("{n}", str(number)).replace("{i}", str(len(episodes) + 1)).replace("{k}", str(k + 1))
            episodes.append({"episodeNumber": number, "name": name, "airDate": iso, "overview": "", "runtime": opts["runtime"], "stillUrl": ""})
            number += 1
    meta = {
        "firstDate": episodes[0]["airDate"] if episodes else "",
        "lastDate": episodes[-1]["airDate"] if episodes else "",
        "summary": (
            f"每周 {'、'.join(WEEKDAY_NAMES[d] for d in opts['weekdays'])} 更新，每次 {opts['perSlot']} 集"
            if opts["pattern"] == "weekly"
            else f"每 {opts['intervalDays']} 天更新一次，每次 {opts['perSlot']} 集"
        ) if episodes else "",
    }
    return {"episodes": episodes, "error": "", "meta": meta}


def apply_episode_filter_words(episodes: List[Dict[str, Any]], words: Any) -> Dict[str, Any]:
    """TMDB-Import 式过滤词：命中标题的集剔除，剩余集重编号但保留原有缺集。

    例 [1,2,3(PV),5] 过滤 3 → [1,2,4]（PV 造成的跳档补上，原本就缺的 4 保留跳档）。
    """
    word_list = [w.strip().lower() for w in (words if isinstance(words, list) else re.split(r"[,，、\s]+", str(words or ""))) if str(w).strip()]
    if not episodes or not word_list:
        return {"episodes": episodes or [], "removed": []}

    def hit(ep: Dict[str, Any]) -> bool:
        name = str(ep.get("name") or "").lower()
        return any(word in name for word in word_list)

    removed = [ep for ep in episodes if hit(ep)]
    if not removed:
        return {"episodes": episodes, "removed": []}
    kept = [ep for ep in episodes if not hit(ep)]
    numbers = {int(ep.get("episodeNumber") or 0) for ep in episodes}
    low, high = min(numbers), max(numbers)
    gaps = [n for n in range(low, high + 1) if n not in numbers]
    out = []
    for index, ep in enumerate(kept):
        below = sum(1 for g in gaps if g < int(ep.get("episodeNumber") or 0))
        out.append({**ep, "episodeNumber": low + index + below})
    return {"episodes": out, "removed": removed}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="国内影视数据源取数 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("episodes", help="自动识别平台抓整季分集")
    p.add_argument("url")
    p.add_argument("--out", help="JSON 落盘路径")
    p.add_argument("--tsv", help="TSV 落盘路径")

    p = sub.add_parser("bilibili-search", help="B站番剧搜索")
    p.add_argument("keyword")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("hongguo-search", help="红果短剧搜索")
    p.add_argument("keyword")

    p = sub.add_parser("douban-suggest", help="豆瓣搜索建议")
    p.add_argument("keyword")

    p = sub.add_parser("douban-detail", help="豆瓣条目详情")
    p.add_argument("douban_id")

    p = sub.add_parser("douban-imdb", help="IMDb 编号反查豆瓣")
    p.add_argument("imdb_id")

    p = sub.add_parser("baike-search", help="百度百科搜索")
    p.add_argument("keyword")

    p = sub.add_parser("baike-detail", help="百科词条详情")
    p.add_argument("url")

    p = sub.add_parser("baike-episodes", help="百科分集剧情")
    p.add_argument("url")
    p.add_argument("--tsv", help="TSV 落盘路径")

    p = sub.add_parser("schedule", help="生成排期分集（周更/固定间隔/单日多集）")
    p.add_argument("--pattern", choices=("weekly", "interval"), default="weekly")
    p.add_argument("--start-date", required=True, help="首播日期 YYYY-MM-DD")
    p.add_argument("--count", type=int, required=True, help="要生成的集数")
    p.add_argument("--weekdays", default="", help="weekly 模式更新日，如「一、四」或 1,4 或 周中/周末")
    p.add_argument("--interval-days", type=int, default=7, help="interval 模式间隔天数")
    p.add_argument("--per-slot", type=int, default=1, help="每个更新日几集（单日多集共用日期）")
    p.add_argument("--start-number", type=int, default=1)
    p.add_argument("--runtime", type=int, default=0)
    p.add_argument("--title-template", default="第{n}集", help="支持 {n}集号 {i}序号 {k}当日第几集")
    p.add_argument("--tsv", help="TSV 落盘路径")

    p = sub.add_parser("episodes-filter", help="按过滤词剔除分集并重编号（PV/预告/花絮）")
    p.add_argument("file", help="分集 JSON 文件（episodes 数组，episodes 子命令的产出）或 - 读 stdin")
    p.add_argument("--words", required=True, help="逗号/顿号/空白分隔的过滤词")
    p.add_argument("--out", help="过滤后 JSON 落盘路径")

    p = sub.add_parser("image", help="按 Referer 表下载图片")
    p.add_argument("url")
    p.add_argument("-o", "--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "episodes":
            matched = match_site_source(args.url)
            if not matched:
                raise RuntimeError("识别不出平台（支持 bilibili/iqiyi/mgtv/qq/youku/hongguo 链接）")
            fetcher, name = matched
            result = fetcher(args.url)
            result["platform"] = name
            result.setdefault("overview", "")
            result.setdefault("cover", "")
            text = json_dumps(result)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text)
                print(f"已写入 {args.out}（{len(result.get('episodes') or [])} 集）")
            else:
                print(text)
            if args.tsv:
                with open(args.tsv, "w", encoding="utf-8") as fh:
                    fh.write(episodes_tsv(result.get("episodes") or []))
                print(f"TSV 已写入 {args.tsv}")
        elif args.command == "bilibili-search":
            rows = map_bilibili_search_results(bili_search_json(args.keyword))[: max(0, args.limit)]
            print(json_dumps(rows))
        elif args.command == "hongguo-search":
            html = site_get_text(f"https://hongguoduanju.com/search/{quote(args.keyword)}")
            data = extract_hongguo_router_data(html)
            loader = (data.get("loaderData") or {}).get("search_page") or (data.get("loaderData") or {}).get("search") or {}
            search_list = None
            for value in (loader.values() if isinstance(loader, dict) else []):
                if isinstance(value, dict) and isinstance(value.get("searchList"), list):
                    search_list = value["searchList"]
                    break
            if search_list is None:
                raise RuntimeError("红果搜索页没有解析到 searchList（页面结构可能已变化）")
            print(json_dumps(map_hongguo_search_list(search_list)))
        elif args.command == "douban-suggest":
            print(json_dumps(douban_suggest(args.keyword)))
        elif args.command == "douban-detail":
            print(json_dumps(douban_detail(args.douban_id)))
        elif args.command == "douban-imdb":
            found = douban_by_imdb(args.imdb_id)
            if not found:
                print(json_dumps({"error": "豆瓣未找到该 IMDb 编号对应条目"}))
                return 1
            print(json_dumps(found))
        elif args.command == "baike-search":
            print(json_dumps(baike_search(args.keyword)))
        elif args.command == "baike-detail":
            print(json_dumps(baike_detail(args.url)))
        elif args.command == "baike-episodes":
            result = baike_episodes(args.url)
            if args.tsv:
                with open(args.tsv, "w", encoding="utf-8") as fh:
                    fh.write(episodes_tsv(result["episodes"]))
                print(f"TSV 已写入 {args.tsv}（{len(result['episodes'])} 集）")
            else:
                print(json_dumps(result))
        elif args.command == "schedule":
            result = build_episode_schedule({
                "pattern": args.pattern,
                "startDate": args.start_date,
                "count": args.count,
                "weekdays": args.weekdays,
                "intervalDays": args.interval_days,
                "perSlot": args.per_slot,
                "startNumber": args.start_number,
                "runtime": args.runtime,
                "titleTemplate": args.title_template,
            })
            if result["error"]:
                print(f"错误：{result['error']}", file=sys.stderr)
                return 1
            if args.tsv:
                with open(args.tsv, "w", encoding="utf-8") as fh:
                    fh.write(episodes_tsv(result["episodes"]))
                print(f"已生成 {len(result['episodes'])} 集（{result['meta']['summary']}，{result['meta']['firstDate']} ~ {result['meta']['lastDate']}），TSV 写入 {args.tsv}")
            else:
                print(json_dumps(result))
        elif args.command == "episodes-filter":
            if args.file == "-":
                data = json.load(sys.stdin)
            else:
                with open(args.file, encoding="utf-8") as fh:
                    data = json.load(fh)
            episodes = data.get("episodes") if isinstance(data, dict) else data
            if not isinstance(episodes, list):
                raise RuntimeError("输入需要是 episodes 数组或 episodes 子命令的产出 JSON")
            result = apply_episode_filter_words(episodes, args.words)
            payload = {"episodes": result["episodes"], "removed": result["removed"]}
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(json_dumps(payload))
                print(f"剔除 {len(result['removed'])} 集，剩余 {len(result['episodes'])} 集，写入 {args.out}")
            else:
                print(json_dumps(payload))
        elif args.command == "image":
            print(download_image(args.url, args.out))
        return 0
    except RuntimeError as err:
        print(f"错误：{err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
