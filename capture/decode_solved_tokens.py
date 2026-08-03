"""解码 quickjs 真实产出的 token，验证跨 token 自洽性（好友 A2/A3 实证）。

输入: data/solved_tokens/solved_tokens.jsonl（get_sentinel_token_via_quickjs 每次成功 dump 一行）

对比同一次注册的 requirements token（request_p）与 solve token（final_p）：
- 真浏览器 timeOrigin（页面加载时刻）是会话内常数 → 两 token 的 time_origin 应一致
- 若每次 Node 进程现算 → 两 token 的 time_origin 漂移（好友 A3）

用法: python capture/decode_solved_tokens.py [--file data/solved_tokens/solved_tokens.jsonl]
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "data" / "solved_tokens" / "solved_tokens.jsonl"

# sentinel p 的已知外壳（手搓 pow 与真实 SDK 同族）: gAAAAAB / gAAAAAC 前缀 + b64 + ~S
_PREFIXES = ("gAAAAAB", "gAAAAAC", "gAAAAAD", "gAAAAAE")


def _pad_b64(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def decode_p(p: str):
    """把 sentinel p 解码为 python 对象；解不出来则返回原始说明字符串。"""
    s = str(p or "").strip()
    for pre in _PREFIXES:
        if s.startswith(pre):
            s = s[len(pre):]
            break
    if s.endswith("~S"):
        s = s[:-2]
    elif s.endswith("~"):
        s = s[:-1]
    # url-safe 与 standard 都试
    for alt in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = alt(_pad_b64(s))
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return raw
        except Exception:
            continue
    return f"<undecodable: {str(p)[:60]}...>"


def _looks_like_timestamp(v) -> bool:
    """epoch 毫秒量级（1.7e12）或秒量级（1.7e9）的数值，可能是 time_origin / Date.now。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    a = abs(v)
    return (1e11 < a < 9e14) or (1e8 < a < 9e11)


def _repr(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}"
    return repr(v)


def analyze(rec: dict) -> None:
    did = rec.get("device_id", "?")
    flow = rec.get("flow", "?")
    ts = rec.get("ts", 0)
    print(f"\n{'=' * 78}")
    print(f"device_id={did}  flow={flow}  ts={ts}  t_len={rec.get('t_len')}")
    print(f"so={'有' if rec.get('so') else '无'}")

    rp = decode_p(rec.get("request_p", ""))
    fp = decode_p(rec.get("final_p", ""))
    print(f"  request_p: {type(rp).__name__}  {rp if isinstance(rp, list) else str(rp)[:100]}")
    print(f"  final_p  : {type(fp).__name__}  {fp if isinstance(fp, list) else str(fp)[:100]}")

    if isinstance(rp, list) and isinstance(fp, list):
        print(f"  request_p 数组长度={len(rp)}  final_p 数组长度={len(fp)}")
        for i in range(max(len(rp), len(fp))):
            a = rp[i] if i < len(rp) else "<none>"
            b = fp[i] if i < len(fp) else "<none>"
            drift = a != b
            tag = ""
            if drift:
                ta, tb = _looks_like_timestamp(a), _looks_like_timestamp(b)
                if ta or tb:
                    tag = "  <-- time_origin 类漂移？"
                else:
                    tag = "  <-- 字段漂移"
            print(f"  [{i:2}] req={_repr(a)}  solve={_repr(b)}{'  | SAME' if not drift else tag}")
    print(f"{'=' * 78}")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FILE
    if not path.exists():
        print(f"没有 dump 文件: {path}")
        print("先注册一次（quickjs 模式），成功后会 dump 到这里。")
        return 1
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        print(f"{path} 为空")
        return 1
    print(f"共 {len(lines)} 次注册的 dump。")
    for l in lines:
        try:
            analyze(json.loads(l))
        except Exception as e:
            print(f"解析失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
