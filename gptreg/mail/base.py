"""收码客户端抽象基类（插件化：新增通道实现 wait_for_otp + 注册到工厂）。

对齐 sentinel_engine 注册表模式：核心(工厂)只认接口，不关心通道内部。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class MailClient(ABC):
    """统一收码接口。子类实现 wait_for_otp 即接入 build_mail_client 工厂。"""

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
