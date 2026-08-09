"""配置加载与路径解析。"""
from __future__ import annotations

import random
import string
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"

_DEFAULTS: dict[str, Any] = {
    "proxy": {
        "default": "http://127.0.0.1:10808",
        "pool": [],
        "dynamic": {
            "enabled": False,
            "template": "",
            "region": "US",
            "rotate_sid": True,
            "sid_len": 8,
            "sticky": 5,
            "chain_via": "http://127.0.0.1:7890",
        },
    },
    "mail": {
        "pool_file": "mail_pool.txt",
        "poll_interval": 3,
        "max_wait": 90,
        "settle_seconds": 5,
        # 单次 OTP 等待。IMAP 秒级读信,但 OpenAI 发码到邮件到达可能 1.5-2.5 分钟
        # (2026-08-06 实测),otp_wait 需覆盖发码延迟,不宜过短(曾 45s 错过到达)
        "otp_wait": 150,
        "otp_max_attempts": 2,
        "used_code_cache": "data/used_otp_codes.json",
        # 唯一默认源: 默认 plus 别名(与 verify_pwd_totp 代码兜底 True 一致)。
        # 历史 _DEFAULTS=False vs 代码兜底 True 冲突——yaml 缺该项时行为不定, 已对齐。
        "use_alias": True,
        "alias_tag_len": 6,
        # 通用第三方 API 接码(号池行 email----api_key, mail_type="api")
        "api_client": {
            "endpoint": "",  # 收码 API URL(空=禁用该来源)
            "method": "POST",
            "request_body": '{"api_key":"{api_key}","email":"{email}","mailbox":"INBOX"}',
            "otp_path": "",  # 响应里 OTP 的 JSON 路径(空=通用扫描)
            "interval": 3,
        },
        # 自托管 cloud-mail(maillab) 号源: 号池行 user@domain, admin 拉码
        "cloud_mail": {
            "base_url": "",
            "admin_email": "",
            "admin_password": "",
            "domains": [],  # 可用域名(号池行用); 空则 list_domains() 查 API
        },
    },
    "browser": {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
        "impersonate": "chrome142",
        "language": "en-US",
        "languages": "en-US,en",
        "accept_language": "en-US,en;q=0.9",
        "request_timeout": 60,
        "screen_width": 1920,
        "screen_height": 1080,
        "hardware_concurrency": 16,
    },
    "protocol": {
        "client_id": "app_X8zY6vW2pQ9tR3dE7nK1jL5gH",
        "scope": (
            "openid email profile offline_access "
            "model.request model.read organization.read organization.write"
        ),
        "audience": "https://api.openai.com/v1",
        "redirect_uri": "https://chatgpt.com/api/auth/callback/openai",
        "sentinel_sv": "20260219f9f6",
        "sentinel_dir": "vendor/sentinel",
        # 注意：此值仅影响 OTP-only 路径(register_otp/cli)与兜底;
        # 主路线 register_pwd 固定 quickjs_pwd_v3 产 t, 不读本项(README 已说明)。
        "sentinel_proxy_port": 1789,
        # pow=默认纯 Python（通常无 so）| browser=真 Chrome token+so（opt-in）
        # 唯一默认源: 与 auth/cli/register_otp 代码兜底 "pow" 一致
        "sentinel_source": "pow",
        # none | xiaopp（小PP HAR so，create 纯协议带头，无浏览器）
        "pow_so_source": "xiaopp",
        "sentinel_browser_headless": True,
        "sentinel_browser_timeout": 60,
        # 空=自动 chain_via → default；也可写 http://127.0.0.1:7890
        "sentinel_browser_proxy": "",
        "sentinel_browser_page": "https://auth.openai.com/about-you",
        "sentinel_browser_local_sdk": False,
    },
    "register": {
        "default_name": "",
        # 统一密码(空=每次随机)。填了则所有注册都用同一密码——半注册邮箱
        # (register 成功但 create/so 失败)可用同一密码手动登录找回, 不再丢密码
        "default_password": "",
        "birthday_year_min": 1995,
        "birthday_year_max": 2005,
        "finalize_attempts": 5,
        # Step B：登录后 me + conversation/init + prepare（不 finalize/不造假）
        "post_login": False,
        # create 400 同 body 重试（对齐资料 zip，不改 sentinel）
        "create_retries": 3,
        "create_retry_sleep": 2.0,
        # pow 遇 registration_disallowed 后 browser 回退（默认关：纯协议；CLI/yaml 可开）
        "create_browser_fallback": False,
    },
    "output": {
        "dir": "output",
        "accounts_jsonl": "accounts.jsonl",
        "tokens_txt": "tokens.txt",
        "emails_txt": "emails.txt",
        "full_lines_txt": "full_lines.txt",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_path(path: str | Path, base: Path | None = None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return (base or ROOT) / p


def _root(cfg: dict[str, Any]) -> Path:
    """项目根路径(注册链路径解析用, 公共工具避免反向依赖)。"""
    return Path(cfg.get("_root") or ".")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    data: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式错误: {cfg_path}")
        data = raw
    cfg = _deep_merge(_DEFAULTS, data)
    cfg["_config_path"] = str(cfg_path)
    cfg["_root"] = str(ROOT)
    return cfg


def pick_proxy(cfg: dict[str, Any], override: str | None = None) -> str:
    """选择本次注册使用的代理 URL（兼容旧调用）。

    动态代理 / 链式隧道请用 gptreg.proxyutil.resolve_proxy。
    override:
      - None: 用配置
      - "": 强制直连
      - 其他: 指定代理
    """
    from gptreg.proxyutil import pick_proxy as _pick

    return _pick(cfg, override)


# 对齐 k12 / starmiaoa / 资料：自然英文名（卫生项；成功号历史乱码名亦能过 create）
_FIRST_NAMES = (
    "James", "Robert", "John", "Michael", "David", "William", "Richard",
    "Mary", "Jennifer", "Linda", "Elizabeth", "Susan", "Jessica", "Sarah",
    "Emily", "Emma", "Olivia", "Sophia", "Liam", "Noah", "Oliver", "Ethan",
    "Daniel", "Matthew", "Anthony", "Mark", "Andrew", "Joshua", "Kevin",
    "Ryan", "Brandon", "Jason", "Mason", "Eric", "Adam",
)
_LAST_NAMES = (
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Martin",
    "Reed", "Cooper", "Ward", "Price", "Foster", "Hayes", "Walsh",
    "Jackson", "Thompson", "White", "Harris", "Clark",
)


def random_display_name() -> str:
    """自然英文姓名（对齐参考项目名池）。非 registration_disallowed 充分修复。"""
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


def random_birthdate(cfg: dict[str, Any]) -> str:
    reg = cfg.get("register", {})
    y_min = int(reg.get("birthday_year_min", 1995))
    y_max = int(reg.get("birthday_year_max", 2005))
    year = random.randint(y_min, y_max)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year}-{month:02d}-{day:02d}"


def pick_password(cfg: dict[str, Any], length: int = 14) -> str:
    """注册密码: config register.default_password(统一密码)或随机(空则随机)。

    统一密码的意义: OpenAI 注册是 per-邮箱状态机, 半注册邮箱(register 成功设密码但
    create/so 失败)不可重设密码; 若每次随机, 首设密码随进程丢失 → 半注册邮箱无法找回。
    统一密码让"register 已设的密码"恒等于已知值 → 可用同一密码手动登录找回。
    """
    pw = str(((cfg or {}).get("register") or {}).get("default_password") or "")
    if pw:
        return pw
    return "".join(
        random.choice(string.ascii_letters + string.digits + "!@#$%")
        for _ in range(max(8, int(length or 14)))
    )
