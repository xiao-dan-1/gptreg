"""收码客户端抽象基类（插件化：新增通道实现 wait_for_otp + 注册到工厂）。

对齐 sentinel_engine 注册表模式：核心(工厂)只认接口，不关心通道内部。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class MailClient(ABC):
    """统一收码接口。子类实现 wait_for_otp 即接入收码工厂。"""

    email: str = ""

    @abstractmethod
    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 3,
        settle_seconds: int = 5,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        """等待 OTP 邮件并返回 6 位验证码；超时抛 MailClientError。"""
        raise NotImplementedError


class MailSource(ABC):
    """邮箱来源插件: 号池行解析 + 收码客户端选择。

    新增号源(如 CloudMail)只需:
      1. 继承本类实现 parse_line / build_client
      2. 注册进 sources.MAIL_SOURCES
    核心(parse_mail_line / build_mail_client)零改动(开闭原则)。
    """

    name: str = ""

    @abstractmethod
    def parse_line(self, raw: str) -> dict | None:
        """解析号池一行 → account dict(含 mail_type); 无法识别返回 None。"""
        raise NotImplementedError

    @abstractmethod
    def build_client(
        self,
        account: dict,
        *,
        proxy: str | None = None,
        impersonate: str = "chrome142",
        cfg: dict | None = None,
    ) -> MailClient:
        """为 account 构建收码客户端。"""
        raise NotImplementedError

    def identity_key(self, account: dict) -> str:
        """共享收件箱排除键(默认 code_url 或 email)。"""
        return account.get("code_url") or account.get("email") or ""
