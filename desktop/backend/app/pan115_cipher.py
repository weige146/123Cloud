"""115 上传接口（uplb 4.0/initupload.php）所需的纯 Python 密码学工具。

按公开标准实现，无第三方依赖：

- AES-128-CBC（FIPS-197）：S-box 由 GF(2^8) 求逆 + 仿射变换程序化生成
- LZ4 block 解压（官方 block 格式规范）：115 的上传响应按 8KB 分帧压缩
- 115 上传签名协议：sig/token 计算、k_ec token、请求体 AES 加密

与 pan115.py 里的 115 RSA 实现同属一套"自包含、零依赖"的思路。
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import zlib
from typing import Any, Dict

__all__ = [
    "aes_cbc_decrypt", "aes_cbc_encrypt", "build_upload_request",
    "decode_upload_response", "ecdh_encode_token",
]


# ---------------------------------------------------------------------------
# AES-128（FIPS-197）
# ---------------------------------------------------------------------------
def _build_sbox() -> list:
    """由 GF(2^8) 乘法逆元 + 仿射变换生成 S-box（FIPS-197 §5.3.2）。"""
    # GF(2^8) 上以 0x1b 为模的多项式乘法
    def gmul(a: int, b: int) -> int:
        product = 0
        for _ in range(8):
            if b & 1:
                product ^= a
            high = a & 0x80
            a = (a << 1) & 0xFF
            if high:
                a ^= 0x1B
            b >>= 1
        return product

    inverse = [0] * 256
    for value in range(1, 256):
        for candidate in range(1, 256):
            if gmul(value, candidate) == 1:
                inverse[value] = candidate
                break

    sbox = []
    for value in range(256):
        x = inverse[value]
        transformed = x
        for _ in range(4):
            x = ((x << 1) | (x >> 7)) & 0xFF
            transformed ^= x
        sbox.append(transformed ^ 0x63)
    return sbox


_SBOX = _build_sbox()
_SBOX_INV = [0] * 256
for _i, _v in enumerate(_SBOX):
    _SBOX_INV[_v] = _i

# GF(2^8) 乘法系数表（MixColumns / InvMixColumns 用）
_XTIME_TABLE = [[None] * 256 for _ in range(3)]


def _xtime(value: int) -> int:
    high = value & 0x80
    value = (value << 1) & 0xFF
    return value ^ 0x1B if high else value


def _gmul(a: int, b: int) -> int:
    product = 0
    while b:
        if b & 1:
            product ^= a
        a = _xtime(a)
        b >>= 1
    return product


_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


class _AES128:
    """仅支持 128 位密钥（115 上传协议只用 AES-128）。"""

    def __init__(self, key: bytes) -> None:
        if len(key) != 16:
            raise ValueError("AES-128 需要 16 字节密钥")
        # 轮密钥扩展（FIPS-197 §5.2）：w[i] = w[i-4] ^ g(w[i-1])（i % 4 == 0）或 w[i-4] ^ w[i-1]
        words = [list(key[i * 4:(i + 1) * 4]) for i in range(4)]
        self.round_keys = []
        for round_index in range(11):
            self.round_keys.append([byte for word in words[round_index * 4:round_index * 4 + 4] for byte in word])
            if round_index == 10:
                break
            prev = words[-1]
            transformed = [
                _SBOX[prev[1]] ^ _RCON[round_index],
                _SBOX[prev[2]],
                _SBOX[prev[3]],
                _SBOX[prev[0]],
            ]
            for i in range(4):
                other = transformed if i == 0 else words[-1]
                words.append([a ^ b for a, b in zip(words[-4], other)])

    def encrypt_block(self, block: bytes) -> bytes:
        state = [list(block[i::4]) for i in range(4)]
        state = self._add_round_key(state, 0)
        for round_index in range(1, 10):
            state = self._sub_bytes(state)
            state = self._shift_rows(state)
            state = self._mix_columns(state)
            state = self._add_round_key(state, round_index)
        state = self._sub_bytes(state)
        state = self._shift_rows(state)
        state = self._add_round_key(state, 10)
        return bytes(state[row][col] for col in range(4) for row in range(4))

    def decrypt_block(self, block: bytes) -> bytes:
        state = [list(block[i::4]) for i in range(4)]
        state = self._add_round_key(state, 10)
        for round_index in range(9, 0, -1):
            state = self._inv_shift_rows(state)
            state = self._inv_sub_bytes(state)
            state = self._add_round_key(state, round_index)
            state = self._inv_mix_columns(state)
        state = self._inv_shift_rows(state)
        state = self._inv_sub_bytes(state)
        state = self._add_round_key(state, 0)
        return bytes(state[row][col] for col in range(4) for row in range(4))

    def _add_round_key(self, state: list, round_index: int) -> list:
        keys = self.round_keys[round_index]
        for col in range(4):
            for row in range(4):
                state[row][col] ^= keys[col * 4 + row]
        return state

    def _sub_bytes(self, state: list) -> list:
        return [[_SBOX[b] for b in row] for row in state]

    def _inv_sub_bytes(self, state: list) -> list:
        return [[_SBOX_INV[b] for b in row] for row in state]

    def _shift_rows(self, state: list) -> list:
        for row in range(1, 4):
            state[row] = state[row][row:] + state[row][:row]
        return state

    def _inv_shift_rows(self, state: list) -> list:
        for row in range(1, 4):
            state[row] = state[row][-row:] + state[row][:-row]
        return state

    def _mix_columns(self, state: list) -> list:
        for col in range(4):
            a0, a1, a2, a3 = (state[row][col] for row in range(4))
            state[0][col] = _gmul(a0, 2) ^ _gmul(a1, 3) ^ a2 ^ a3
            state[1][col] = a0 ^ _gmul(a1, 2) ^ _gmul(a2, 3) ^ a3
            state[2][col] = a0 ^ a1 ^ _gmul(a2, 2) ^ _gmul(a3, 3)
            state[3][col] = _gmul(a0, 3) ^ a1 ^ a2 ^ _gmul(a3, 2)
        return state

    def _inv_mix_columns(self, state: list) -> list:
        for col in range(4):
            a0, a1, a2, a3 = (state[row][col] for row in range(4))
            state[0][col] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
            state[1][col] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
            state[2][col] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
            state[3][col] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)
        return state


def _pad(data: bytes) -> bytes:
    pad_size = -len(data) & 15
    return data + bytes((pad_size,) * pad_size)


def _unpad(data: bytes) -> bytes:
    if data and 0 < data[-1] < 16 and data[-data[-1]:] == bytes((data[-1],)) * data[-1]:
        return data[:-data[-1]]
    return data


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = _AES128(key)
    view = _pad(data)
    output = bytearray()
    previous = iv
    for offset in range(0, len(view), 16):
        block = bytes(p ^ l for p, l in zip(view[offset:offset + 16], previous))
        previous = cipher.encrypt_block(block)
        output += previous
    return bytes(output)


def aes_cbc_decrypt(cipher_data: bytes, key: bytes, iv: bytes) -> bytes:
    cipher = _AES128(key)
    output = bytearray()
    previous = iv
    for offset in range(0, len(cipher_data), 16):
        block = cipher_data[offset:offset + 16]
        output += bytes(p ^ l for p, l in zip(cipher.decrypt_block(block), previous))
        previous = block
    return _unpad(bytes(output))


# ---------------------------------------------------------------------------
# LZ4 block 解压（官方 block 格式）
# ---------------------------------------------------------------------------
def lz4_block_decompress(src: bytes) -> bytes:
    out = bytearray()
    offset = 0
    total = len(src)
    while offset < total:
        token = src[offset]
        offset += 1
        literal_len = token >> 4
        if literal_len == 15:
            while True:
                byte = src[offset]
                offset += 1
                literal_len += byte
                if byte != 255:
                    break
        out += src[offset:offset + literal_len]
        offset += literal_len
        if offset >= total:
            break
        match_offset = src[offset] | (src[offset + 1] << 8)
        offset += 2
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                byte = src[offset]
                offset += 1
                match_len += byte
                if byte != 255:
                    break
        match_len += 4
        start = len(out) - match_offset
        if start < 0:
            raise ValueError("LZ4 block 解压遇到非法回溯距离")
        for index in range(match_len):
            out.append(out[start + index])
    return bytes(out)


def lz4_decompress(source: bytes) -> bytes:
    """115 的分帧：[2 字节 LE 块长][块数据]…，每块解出最多 0x2000 字节。"""
    data = b""
    view = memoryview(source)
    position = 0
    while position + 2 <= len(view):
        block_len = view[position] + (view[position + 1] << 8)
        if block_len == 0:
            break
        data += lz4_block_decompress(bytes(view[position + 2:position + 2 + block_len]))
        position += 2 + block_len
    return data


# ---------------------------------------------------------------------------
# 115 上传签名协议（uplb 4.0/initupload.php）
# ---------------------------------------------------------------------------
CRC_SALT = b"^j>WD3Kr?J2gLFjD4W2y@"
MD5_SALT = b"Qclm8MGWUv59TnrR0XPg"
AES_PUBKEY = b"\x1d\x03\x0e\x80\xa1x\xdc\xee\xce\xcd\xa3w\xde\x12\x8d\x8e\xd9\xdd\xcfU\xaea\xedF\xea\x12\x1a\x1c\xfc\x81"
AES_KEY = b"\xfb\x1a\x19\xd6R\xf5\xaa\xf7\xbce\x1d\x0fi\xbfB/"
AES_IV = b"i\xbfB/I\x96\x05P\xa0\xadD\xec4F\xcbL"


def ecdh_encode_token(timestamp: int) -> bytes:
    token = bytearray()
    token += AES_PUBKEY[:15]
    token += b"\x00s\x00\x00\x00"
    token += timestamp.to_bytes(4, "little")
    token += AES_PUBKEY[15:]
    token += b"\x00\x01\x00\x00\x00"
    token += (zlib.crc32(CRC_SALT + bytes(token)) & 0xFFFFFFFF).to_bytes(4, "little")
    return base64.b64encode(bytes(token))


def decode_upload_response(content: bytes) -> Dict[str, Any]:
    """解密 initupload 响应：AES-CBC 解密 + LZ4 解压 + JSON。

    服务器会在密文尾部附加少量字节（总长不一定是 16 的倍数），
    按 p115client 的做法截断到 16 对齐后再解密。
    """
    aligned = content[:len(content) & -16]
    data = aes_cbc_decrypt(aligned, AES_KEY, AES_IV)
    text = lz4_decompress(data)
    return json.loads(text)


def build_upload_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """构建 initupload 请求：计算 sig/token，返回 {"params": k_ec, "data": 密文体}。"""
    timestamp = payload["t"] = int(time.time())
    sig_sha1 = hashlib.sha1(str(payload["userkey"]).encode("ascii"))
    sig_sha1.update(
        hashlib.sha1(
            "{userid}{fileid}{target}0".format_map(payload).encode("ascii")
        ).hexdigest().encode("ascii")
    )
    sig_sha1.update(b"000000")
    payload["sig"] = sig_sha1.hexdigest().upper()

    token_md5 = hashlib.md5(MD5_SALT)
    token_md5.update(
        "{fileid}{filesize}{sign_key}{sign_val}{userid}{t}".format_map(payload).encode("ascii")
    )
    token_md5.update(hashlib.md5(str(int(str(payload["userid"]).split("_", 1)[0])).encode("ascii")).hexdigest().encode("ascii"))
    token_md5.update(str(payload["appversion"]).encode("ascii"))
    payload["token"] = token_md5.hexdigest()

    body = "&".join(
        f"{key}={value}"
        for key, value in sorted((k, v) for k, v in payload.items() if v)
    ).encode("latin-1")
    return {
        "params": {"k_ec": ecdh_encode_token(timestamp).decode("ascii")},
        "data": aes_cbc_encrypt(body, AES_KEY, AES_IV),
    }
