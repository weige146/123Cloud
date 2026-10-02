"""影库「123 助手式」导入的二级链接展开。

用户粘贴的二级链接（短链）解析后只有一个条目，就是种子文件本身（`.123fastlink.json`
/ `.123fastlink.txt`），真正的文件清单在种子内容里。展开流程与油猴脚本
`saveSecondaryFastlink` 同语义：

    按 etag 把种子秒传到自己网盘的临时目录 → 取直链读回文本 → 解析 → 临时文件移入回收站

123 开放平台没有「按 etag 直接下载」的接口（`download_info` 只认 fileId），所以必须
先转存再下载。转存过的临时文件一律在读完后移入回收站（含上次失败留下的同名残留），
不给用户网盘攒垃圾；展开失败也只报文案，不留半成品。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from . import movie_library

logger = logging.getLogger(__name__)

# 临时目录：默认落在网盘「秒传」目录下的一层专用子目录，用完即回收
DEFAULT_SEED_TEMP_PATH = "秒传"
SEED_TEMP_SUBDIR = "影库导入临时"
# 种子文件本身是文本清单，正常几十 KB 到几 MB；超出这个上限说明条目对不上，不是种子
MAX_SEED_BYTES = 32 * 1024 * 1024
# 粘贴框一次最多导入多少条（与批量导入路由的 50 个文件同档）
MAX_PASTE_BYTES = 64 * 1024 * 1024
# 文件导入的二级链接预检只读这么大的文件：短链本身必然很小（种子清单 KB 级），
# 大文件直接走原有流式/整读路径，不为省一次网络请求白读几十 MB 磁盘内容
SEED_FILE_PRECHECK_LIMIT = 8 * 1024 * 1024
MAX_PAYLOADS = 50
# 二级链接里再套二级链接的嵌套护栏（正常只有一层）
MAX_SECONDARY_DEPTH = 3


class SeedResolveError(ValueError):
    """二级链接展开失败。继承 ValueError，路由按「这条导入失败」呈现，不炸整批。"""


def seed_temp_parts(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """临时目录路径拆段：`秒传/影库导入临时` → ["秒传", "影库导入临时"]（网盘从根目录建起）。"""
    base = str((config or {}).get("seedTempPath") or "").strip() or DEFAULT_SEED_TEMP_PATH
    parts = [part for part in base.replace("\\", "/").split("/") if part and part not in (".", "..")]
    return parts + [SEED_TEMP_SUBDIR]


async def read_url_text(
    url: str,
    *,
    max_bytes: int = MAX_SEED_BYTES,
    timeout: float = 60.0,
    transport: Any = None,
) -> str:
    """流式读回直链文本，超过上限立即中断（防把大文件当种子整包拉进内存）。
    transport 仅供回归测试注入假响应，线上不传。"""
    buffer = bytearray()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise SeedResolveError(f"下载种子文件失败（HTTP {response.status_code}）")
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                buffer += chunk
                if len(buffer) > max_bytes:
                    raise SeedResolveError(
                        f"种子文件超过 {max_bytes // (1024 * 1024)}MB，不像秒传清单，已停止读取")
    try:
        return buffer.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SeedResolveError("种子文件不是文本内容，无法展开")


async def find_seed_in_dir(client: Any, parent_id: str, name: str, size: int) -> Optional[int]:
    """临时目录里已有同名同体积文件＝上次展开没清掉的残留，直接复用，不再转存第二份
    （否则 123 会生成「名字 (1)」副本，越积越多）。"""
    try:
        existing = await client.list_files(parent_id)
    except Exception as error:
        logger.warning("影库导入：二级链接临时目录列目录失败（按没有残留处理）：%s", error)
        return None
    for item in existing:
        if not isinstance(item, dict) or int(item.get("type") or 0) != 0:
            continue
        if str(item.get("name") or item.get("fileName") or "") != name:
            continue
        recorded = int(item.get("size") or 0)
        if size and recorded and recorded != int(size):
            continue
        file_id = int(item.get("fileId") or item.get("id") or 0)
        if file_id:
            return file_id
    return None


async def fetch_seed_text(
    seed: Dict[str, Any],
    *,
    client: Any,
    temp_parts: List[str],
    read_text: Optional[Callable[[str], Any]] = None,
) -> str:
    """取回一条二级链接的种子内容（转存 → 下载 → 移入回收站）。"""
    # 读文本函数在调用时才取模块属性（默认参数写死会把旧函数绑进签名，回归里 monkeypatch 不生效）
    reader = read_text or read_url_text
    name = str(seed.get("fileName") or "").strip()
    etag = movie_library.etag_hex(str(seed.get("etag") or ""))
    size = int(seed.get("size") or 0)
    if not name or not etag or size <= 0:
        raise SeedResolveError("这条二级链接缺少种子文件名、MD5 或体积，无法展开")
    parent_id = await client.ensure_path("0", temp_parts)
    file_id = await find_seed_in_dir(client, parent_id, name, size)
    if not file_id:
        try:
            file_id = await client.md5_reuse(parent_id, name, etag, size)
        except Exception as error:
            raise SeedResolveError(f"种子文件转存失败：{error}")
    if not file_id:
        raise SeedResolveError("网盘里没有这条二级链接的种子文件（秒传未命中），"
                               "请先在 123 助手里转存种子文件，或改用完整秒传链接导入")
    try:
        url = await client.download_info(int(file_id))
        text = await reader(url)
    finally:
        try:
            await client.trash_files([int(file_id)])
        except Exception as error:
            logger.warning("影库导入：二级链接临时种子文件移入回收站失败：%s", error)
    return text


async def expand_payload(
    payload: Dict[str, Any],
    *,
    client: Any,
    temp_parts: List[str],
    read_text: Optional[Callable[[str], Any]] = None,
    depth: int = 0,
    notes: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """解析结果若是二级链接就联网展开种子内容（种子内容还能再套一层，有深度护栏）。
    返回 (最终 payload, 展开说明)，说明用于导入结果提示。"""
    collected = list(notes or [])
    current = payload
    seed = movie_library.fastlink_seed_entry(current)
    while seed:
        if depth >= MAX_SECONDARY_DEPTH:
            raise SeedResolveError("二级链接嵌套层数过多，已停止展开")
        text = await fetch_seed_text(seed, client=client, temp_parts=temp_parts, read_text=read_text)
        current = movie_library.parse_library_content(text)
        depth += 1
        file_count = len(current.get("files") or [])
        collected.append(f"已展开二级链接「{seed.get('fileName')}」：{file_count} 个条目")
        seed = movie_library.fastlink_seed_entry(current)
    return current, collected
