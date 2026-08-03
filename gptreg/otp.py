"""OTP 识别与抽取（多语言、多字段兼容）。"""
from __future__ import annotations

import re

_OPENAI_SENDER = "openai"
_OPENAI_KEYWORDS = (
    "chatgpt",
    "openai",
    "verification code",
    "code is",
    "your code",
    "verify your email",
    "代码",
    "验证码",
    "确认码",
    "認証コード",
    "検証コード",
    "確認コード",
    "인증 코드",
)
_OTP_CONTEXT = (
    "code",
    "verify",
    "verification",
    "代码",
    "验证",
    "确认",
    "コード",
    "認証",
    "검증",
    "코드",
    "인증",
)
_OTP_RE = re.compile(r"\b(\d{6})\b")
_SIX_DIGIT_RE = re.compile(r"(?<![\w#])(\d{6})(?![\w])")


def _field(item: dict, *names: str) -> str:
    for name in names:
        if "." in name:
            value: object = item
            for part in name.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if isinstance(value, str) and value:
                return value
        else:
            value = item.get(name)
            if isinstance(value, str) and value:
                return value
    return ""


def looks_like_openai_email(item: dict) -> bool:
    sender = _field(
        item, "sendEmail", "from", "fromEmail", "from.emailAddress.address", "From.EmailAddress.Address"
    ).lower()
    sender_name = _field(
        item, "sendName", "fromName", "from.emailAddress.name", "From.EmailAddress.Name"
    ).lower()
    subject = _field(item, "subject", "Subject").lower()
    text = _field(item, "text", "bodyPreview", "bodyText", "BodyPreview").lower()
    content = _field(
        item, "content", "body", "html", "body.content", "bodyHtml", "Body.Content"
    ).lower()
    if _OPENAI_SENDER in sender or _OPENAI_SENDER in sender_name:
        return True
    return any(k in s for s in (subject, text, content) for k in _OPENAI_KEYWORDS)


def extract_otp(item: dict) -> str | None:
    subject = _field(item, "subject", "Subject")
    if subject:
        codes = _OTP_RE.findall(subject)
        if len(codes) == 1:
            return codes[0]

    candidates = [
        _field(item, "text", "bodyPreview", "bodyText", "BodyPreview"),
        _field(item, "content", "html", "body", "body.content", "bodyHtml", "Body.Content"),
    ]
    for body in candidates:
        if not body:
            continue
        if "<" in body and ">" in body:
            body = re.sub(r"<[^>]+>", " ", body)
        all_codes = _OTP_RE.findall(body)
        if not all_codes:
            continue
        lower = body.lower()
        for code in all_codes:
            idx = lower.find(code)
            if idx < 0:
                continue
            window = lower[max(0, idx - 60) : idx + 66]
            if any(k.lower() in window for k in _OTP_CONTEXT):
                return code
        return all_codes[0]
    return None


def extract_code_from_any(obj) -> str | None:
    """从 get-code API 的 JSON/文本响应中抽 6 位码。"""
    if obj is None:
        return None
    if isinstance(obj, (int, float)):
        return extract_code_from_any(str(obj))
    if isinstance(obj, str):
        m = _SIX_DIGIT_RE.search(obj)
        return m.group(1) if m else None
    if isinstance(obj, dict):
        for key in (
            "code",
            "otp",
            "verify_code",
            "verification_code",
            "data",
            "message",
            "msg",
            "text",
        ):
            code = extract_code_from_any(obj.get(key))
            if code:
                return code
        for value in obj.values():
            code = extract_code_from_any(value)
            if code:
                return code
    if isinstance(obj, (list, tuple)):
        for item in obj:
            code = extract_code_from_any(item)
            if code:
                return code
    return None
