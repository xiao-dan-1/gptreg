# -*- coding: utf-8 -*-
"""oneshot: align create so-header with 神奇的小PP HAR so (pure protocol, no browser)."""
from __future__ import annotations

import json
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def patch_sentinel() -> None:
    p = ROOT / "gptreg" / "sentinel.py"
    t = p.read_text(encoding="utf-8")

    old_doc = (
        "对照「神奇的小PP」protocol_register：\n"
        "- 可学：Datadog 头、sv 版本、requirements ~S、answer SDK 路径、create 拒建后真 so\n"
        "- 禁止：_HAR_SO / 随机 so 假 so-token（与我们假 so 过滤冲突）\n"
    )
    new_doc = (
        "对照「神奇的小PP」protocol_register：\n"
        "- 已学：Datadog、sv、~S、answer SDK、create 侧 so-token 结构\n"
        "- pow_so_source=xiaopp：create 带小PP 同款 HAR so（纯协议，无浏览器）\n"
        "- 仍过滤 SyntaxError/jsdom 假 so；browser 真 so 仍走 browser_sentinel\n"
    )
    if old_doc not in t:
        if "pow_so_source=xiaopp" not in t:
            raise SystemExit("sentinel doc block missing")
    else:
        t = t.replace(old_doc, new_doc)

    marker = '_MAX_POW_ATTEMPTS = 500_000\n'
    if "_XIAOPP_HAR_SO" not in t:
        if marker not in t:
            raise SystemExit("MAX_POW marker missing")
        har_block = (
            '_MAX_POW_ATTEMPTS = 500_000\n'
            "\n"
            "# 神奇的小PP protocol_register/sentinel.py · 对齐 sentinel.go HAR 抓包 so（纯协议无浏览器）\n"
            "# 仅当 protocol.pow_so_source=xiaopp 时用于 openai-sentinel-so-token；不是 sessionObserverToken。\n"
            "_XIAOPP_HAR_SO = (\n"
            '    "QhccBRcGGxQDF29nCW1vdFpxZlFgf2xkDXJtQWBib1FKCAwaGwceGAEHDAwbdm9zBBcCFAsbGAUbDwx1a0"\n'
            '    "ZLd2hSbH54AWN3UQ1tdU4FTHdoUmx+eAgTFBUXGBgLFxQUenQTCxsZDAcOGxYBGw8MdUFGd3doRmNxaFZn"\n'
            '    "dHtea3VeeHl0aHQTFBUXGgQXBh0UAxdtZwQIDBobDRgYCgIMDBt2fwsEFwIUAQIABwwXFBR6ZBMLGxkMAQ"\n'
            '    "obHQEbDwx1eHx5dGgBbHFeVmxya0ZrcmgIExQVFx8BFw0MDBt2b39ud38Ce3JJVXh0Rll8dm8LBBcCFAsM"\n'
            '    "AAEOFxQUenRZUntkFgsbGQwODBsfDhsPDHJOCBMUFRcdGAANDAwbdm9/XBcCFA8DAAEPFxQUfVJ7bH50SX"\n'
            '    "BxUnd8e2cac3pSHld7Qm8LGxkMAggbGwUbDwx1XggTFBUXGAcXAxoUAxdtdEptb1F8cmZRaHxvdFJxbUF0"\n'
            '    "VG93DQgMGhsMHBgABAwMG3F8RVp0f1l/cm97cHRsXXtxb0Fjd393BBcCFAwGAA8OFxQUemddV3tkGncbSA=="\n'
            ")\n"
            "\n"
            "# create / user_register 等需要 so 头的 flow（对齐小PP）\n"
            "_XIAOPP_SO_FLOWS = frozenset({\n"
            '    "oauth_create_account",\n'
            '    "username_password_create",\n'
            "})\n"
        )
        t = t.replace(marker, har_block, 1)

    if "def build_xiaopp_so_header" not in t:
        old_build = (
            "def build_so_header(\n"
            '    token_json: str, device_id: str, flow: str, challenge_token: str = ""\n'
            ") -> str | None:\n"
            "    try:\n"
            "        parsed = json.loads(token_json)\n"
            "    except Exception:\n"
            "        return None\n"
            '    so_value = parsed.get("so")\n'
            "    if not so_value:\n"
            "        return None\n"
            "    return json.dumps(\n"
            "        {\n"
            '            "so": so_value,\n'
            '            "c": parsed.get("c") or challenge_token or "",\n'
            '            "id": device_id or parsed.get("id") or "",\n'
            '            "flow": flow or parsed.get("flow") or "",\n'
            "        },\n"
            '        separators=(",", ":"),\n'
            "        ensure_ascii=False,\n"
            "    )\n"
        )
        new_build = (
            "def build_xiaopp_so_header(\n"
            "    *,\n"
            "    c: str,\n"
            "    device_id: str,\n"
            "    flow: str,\n"
            "    so_value: str | None = None,\n"
            ") -> str:\n"
            '    """小PP openai-sentinel-so-token：{so: HAR, c, id, flow}。纯协议，无浏览器。"""\n'
            "    return json.dumps(\n"
            "        {\n"
            '            "so": so_value or _XIAOPP_HAR_SO,\n'
            '            "c": c or "",\n'
            '            "id": device_id or "",\n'
            '            "flow": flow or "",\n'
            "        },\n"
            '        separators=(",", ":"),\n'
            "        ensure_ascii=False,\n"
            "    )\n"
            "\n"
            "\n"
            "def build_so_header(\n"
            '    token_json: str, device_id: str, flow: str, challenge_token: str = ""\n'
            ") -> str | None:\n"
            '    """从 token JSON 内嵌 so 字段包装 so-header；无 so 字段则 None。"""\n'
            "    try:\n"
            "        parsed = json.loads(token_json)\n"
            "    except Exception:\n"
            "        return None\n"
            '    so_value = parsed.get("so")\n'
            "    if not so_value:\n"
            "        return None\n"
            "    return json.dumps(\n"
            "        {\n"
            '            "so": so_value,\n'
            '            "c": parsed.get("c") or challenge_token or "",\n'
            '            "id": device_id or parsed.get("id") or "",\n'
            '            "flow": flow or parsed.get("flow") or "",\n'
            "        },\n"
            '        separators=(",", ":"),\n'
            "        ensure_ascii=False,\n"
            "    )\n"
            "\n"
            "\n"
            "def resolve_pow_so_header(\n"
            "    token_json: str,\n"
            "    device_id: str,\n"
            "    flow: str,\n"
            "    *,\n"
            '    pow_so_source: str = "none",\n'
            ") -> str | None:\n"
            '    """pow 路径 so-header。\n'
            "\n"
            "    pow_so_source:\n"
            "      - none: 仅 token 内嵌 so（通常无）\n"
            "      - xiaopp: create 等 flow 用小PP HAR so + 本次 /req 的 c\n"
            '    """\n'
            '    mode = (pow_so_source or "none").strip().lower()\n'
            '    if mode in {"xiaopp", "har", "har_so", "pp", "xiaopp_har"}:\n'
            "        if flow not in _XIAOPP_SO_FLOWS:\n"
            "            return build_so_header(token_json, device_id, flow)\n"
            "        try:\n"
            "            parsed = json.loads(token_json)\n"
            "        except Exception:\n"
            "            parsed = {}\n"
            '        c = str((parsed or {}).get("c") or "").strip()\n'
            "        return build_xiaopp_so_header(c=c, device_id=device_id, flow=flow)\n"
            "    return build_so_header(token_json, device_id, flow)\n"
        )
        if old_build not in t:
            raise SystemExit("build_so_header block missing")
        t = t.replace(old_build, new_build)

    p.write_text(t, encoding="utf-8")
    print("sentinel OK")


def patch_auth() -> None:
    ap = ROOT / "gptreg" / "auth.py"
    at = ap.read_text(encoding="utf-8")

    if "resolve_pow_so_header" not in at:
        if "build_so_header," in at:
            at = at.replace(
                "build_so_header,",
                "build_so_header,\n    resolve_pow_so_header,",
            )
        elif "from gptreg.sentinel import (" in at:
            at = at.replace(
                "from gptreg.sentinel import (\n",
                "from gptreg.sentinel import (\n    resolve_pow_so_header,\n",
            )
        else:
            raise SystemExit("cannot find sentinel import in auth.py")

    old_make = (
        '    so = build_so_header(token, session.device_id, flow, "")\n'
        '    if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):\n'
        '        logger.warning("[Sentinel] so-header 含 SyntaxError，丢弃")\n'
        "        so = None\n"
        "\n"
        "    # 服务端要求 so 但纯 pow 未产：只告警，不伪造\n"
        '    if chatreq_obs.get("so_required") and not so:\n'
        "        logger.warning(\n"
        '            "[Sentinel] chatReq 要求 so 但 pow 路径无 so flow=%s collector_dx_len=%s",\n'
        "            flow,\n"
        '            chatreq_obs.get("so_collector_dx_len"),\n'
        "        )\n"
        "\n"
        "    logger.info(\n"
        '        "[Sentinel] headers ready flow=%s mode=pow has_so=%s so_len=%s so_required=%s collector_dx_len=%s",\n'
        "        flow,\n"
        "        bool(so),\n"
        "        len(so or \"\"),\n"
        '        chatreq_obs.get("so_required"),\n'
        '        chatreq_obs.get("so_collector_dx_len"),\n'
        "    )\n"
        "    session._last_sentinel_meta = {  # type: ignore[attr-defined]\n"
        '        "mode": "pow",\n'
        '        "has_so": bool(so),\n'
        '        "so_len": len(so or ""),\n'
        '        "chatreq": chatreq_obs,\n'
        "    }\n"
    )
    new_make = (
        "    proto = session.cfg.get(\"protocol\") or {}\n"
        '    pow_so_source = str(proto.get("pow_so_source") or "none").strip().lower()\n'
        "    so = resolve_pow_so_header(\n"
        "        token, session.device_id, flow, pow_so_source=pow_so_source\n"
        "    )\n"
        "    # 仅丢 SyntaxError/jsdom 假 so；小PP HAR so 放行（pow_so_source=xiaopp）\n"
        '    if so and ("SyntaxError" in so or "MDogU3ludGF4" in so):\n'
        '        logger.warning("[Sentinel] so-header 含 SyntaxError，丢弃")\n'
        "        so = None\n"
        "\n"
        '    if chatreq_obs.get("so_required") and not so:\n'
        "        logger.warning(\n"
        '            "[Sentinel] chatReq 要求 so 但 pow 路径无 so flow=%s collector_dx_len=%s pow_so_source=%s",\n'
        "            flow,\n"
        '            chatreq_obs.get("so_collector_dx_len"),\n'
        "            pow_so_source,\n"
        "        )\n"
        "\n"
        "    logger.info(\n"
        '        "[Sentinel] headers ready flow=%s mode=pow has_so=%s so_len=%s so_required=%s pow_so_source=%s collector_dx_len=%s",\n'
        "        flow,\n"
        "        bool(so),\n"
        '        len(so or ""),\n'
        '        chatreq_obs.get("so_required"),\n'
        "        pow_so_source,\n"
        '        chatreq_obs.get("so_collector_dx_len"),\n'
        "    )\n"
        "    session._last_sentinel_meta = {  # type: ignore[attr-defined]\n"
        '        "mode": "pow",\n'
        '        "has_so": bool(so),\n'
        '        "so_len": len(so or ""),\n'
        '        "pow_so_source": pow_so_source,\n'
        '        "chatreq": chatreq_obs,\n'
        "    }\n"
    )
    if old_make not in at:
        if "pow_so_source" in at and "resolve_pow_so_header" in at:
            print("auth already patched")
        else:
            raise SystemExit("auth make_sentinel block missing")
    else:
        at = at.replace(old_make, new_make)

    ap.write_text(at, encoding="utf-8")
    print("auth OK")


def patch_config() -> None:
    cp = ROOT / "gptreg" / "config.py"
    ct = cp.read_text(encoding="utf-8")
    if "pow_so_source" not in ct:
        needle = '"sentinel_source": "pow",\n'
        insert = (
            '"sentinel_source": "pow",\n'
            '        # none | xiaopp（小PP HAR so，create 纯协议带头，无浏览器）\n'
            '        "pow_so_source": "xiaopp",\n'
        )
        if needle not in ct:
            raise SystemExit("config.py sentinel_source missing")
        ct = ct.replace(needle, insert, 1)
        cp.write_text(ct, encoding="utf-8")
        print("config.py OK")
    else:
        print("config.py already has pow_so_source")

    yp = ROOT / "config.yaml"
    yt = yp.read_text(encoding="utf-8")
    if "pow_so_source" not in yt:
        needle = '  sentinel_source: "pow"\n'
        insert = (
            '  sentinel_source: "pow"\n'
            "  # none=不带 so | xiaopp=小PP HAR so（纯协议 create 头，无浏览器）\n"
            '  pow_so_source: "xiaopp"\n'
        )
        if needle not in yt:
            # alternate quote style
            needle = "  sentinel_source: pow\n"
            insert = (
                "  sentinel_source: pow\n"
                "  # none=不带 so | xiaopp=小PP HAR so（纯协议 create 头，无浏览器）\n"
                "  pow_so_source: xiaopp\n"
            )
        if needle not in yt:
            raise SystemExit("config.yaml sentinel_source missing")
        yt = yt.replace(needle, insert, 1)
        yp.write_text(yt, encoding="utf-8")
        print("config.yaml OK")
    else:
        print("config.yaml already has pow_so_source")


def smoke() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    for rel in ("gptreg/sentinel.py", "gptreg/auth.py", "gptreg/config.py"):
        py_compile.compile(str(ROOT / rel), doraise=True)

    from gptreg.config import load_config
    from gptreg.sentinel import _XIAOPP_HAR_SO, resolve_pow_so_header

    tok = json.dumps(
        {
            "p": "x",
            "t": "",
            "c": "C123",
            "id": "D",
            "flow": "oauth_create_account",
        },
        separators=(",", ":"),
    )
    so = resolve_pow_so_header(tok, "D", "oauth_create_account", pow_so_source="xiaopp")
    assert so and "QhccBRcG" in so and '"c":"C123"' in so, so
    assert resolve_pow_so_header(tok, "D", "oauth_create_account", pow_so_source="none") is None
    assert resolve_pow_so_header(tok, "D", "authorize_continue", pow_so_source="xiaopp") is None
    cfg = load_config()
    assert cfg["protocol"].get("pow_so_source") == "xiaopp"
    assert cfg["protocol"].get("sentinel_source") == "pow"
    assert cfg["register"].get("create_browser_fallback") is False
    print("SMOKE_OK HAR_len=", len(_XIAOPP_HAR_SO))


if __name__ == "__main__":
    patch_sentinel()
    patch_auth()
    patch_config()
    smoke()
    print("done")
