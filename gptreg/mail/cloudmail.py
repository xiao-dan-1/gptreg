"""自托管 cloud-mail(maillab) 号源插件: admin 拉取邮箱验证码。

API(源自 gpt_register 集成):
  POST {base}/api/login           admin 登录 {email, password} → data.token
  GET  {base}/api/allEmail/list   admin 拉指定邮箱邮件
       params: type=receive, accountEmail=<邮箱>, size, emailId=0, timeSort=0
       header: Authorization: <token>（无 Bearer 前缀）
       响应: data.list（每行含 subject/content/text/raw）

号池行: 单段邮箱 `user@xdauv.xyz`（无 ---- 分隔）。收码用 admin 拉该地址邮件,
一个邮箱一个 OpenAI 账号(注册不用 plus 别名)。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from curl_cffi import requests as cr

from gptreg.mail.base import MailClient, MailSource
from gptreg.mail.otp_cache import MailClientError
from gptreg.otp import extract_otp

logger = logging.getLogger(__name__)


class CloudMailClient(MailClient):
    """cloud-mail 收码: admin 登录 → allEmail/list 拉码。"""

    def __init__(
        self,
        account: dict[str, Any],
        cfg: dict[str, Any] | None = None,
        proxy: str | None = None,
        impersonate: str = "chrome142",
        timeout: int = 30,
    ):
        self.email = account.get("email") or ""
        self.proxy = proxy
        self.impersonate = impersonate
        self.timeout = timeout
        self.cfg = cfg or {}
        cm = (self.cfg.get("mail") or {}).get("cloud_mail") or {}
        self.base_url = str(cm.get("base_url") or "").rstrip("/")
        self.admin_email = str(cm.get("admin_email") or "")
        self.admin_password = str(cm.get("admin_password") or "")
        self._token: str | None = None

    def _proxies(self) -> dict | None:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        if not self.base_url or not self.admin_email or not self.admin_password:
            raise MailClientError("cloud_mail 未配置 base_url/admin_email/admin_password (config mail.cloud_mail)")
        r = cr.post(
            f"{self.base_url}/api/login",
            json={"email": self.admin_email, "password": self.admin_password},
            timeout=self.timeout, impersonate=self.impersonate, proxies=self._proxies(),
        )
        d = r.json() if r.status_code == 200 else {}
        if d.get("code") != 200:
            raise MailClientError(f"cloud_mail admin 登录失败: {str(d)[:120]}")
        data = d.get("data") or {}
        token = str(data.get("token") or data.get("jwt") or "")
        if not token:
            raise MailClientError(f"cloud_mail 登录未返回 token: {str(d)[:120]}")
        self._token = token
        return token

    def list_domains(self) -> list[str]:
        """可用域名: 优先用 config mail.cloud_mail.domains, 空则查 API
        (GET /api/setting/query → domainList, 去 @ 前缀)。"""
        cm = (self.cfg.get("mail") or {}).get("cloud_mail") or {}
        configured = cm.get("domains") or []
        if configured:
            return [str(x).lstrip("@") for x in configured if str(x).strip()]
        token = self._ensure_token()
        r = cr.get(
            f"{self.base_url}/api/setting/query",
            headers={"Authorization": token},
            timeout=self.timeout, impersonate=self.impersonate, proxies=self._proxies(),
        )
        d = r.json() if r.status_code == 200 else {}
        if d.get("code") != 200:
            raise MailClientError(f"cloud_mail setting/query 失败: {str(d)[:120]}")
        data = d.get("data") or {}
        dl = data.get("domainList") or []
        return [str(x).lstrip("@") for x in dl if str(x).strip()]

    def _fetch_mails(self) -> list[dict[str, Any]]:
        token = self._ensure_token()
        r = cr.get(
            f"{self.base_url}/api/allEmail/list",
            params={"type": "receive", "accountEmail": self.email,
                    "size": 20, "emailId": 0, "timeSort": 0},
            headers={"Authorization": token},
            timeout=self.timeout, impersonate=self.impersonate, proxies=self._proxies(),
        )
        d = r.json() if r.status_code == 200 else {}
        if d.get("code") != 200:
            logger.warning("[CloudMail] 拉邮件失败: %s", str(d)[:120])
            return []
        return (d.get("data") or {}).get("list") or []

    def wait_for_otp(
        self,
        after_ts: float | None = None,
        timeout: int = 90,
        interval: int = 3,
        settle_seconds: int = 5,
        exclude_codes: set[str] | None = None,
        on_poll: Callable[[dict], None] | None = None,
    ) -> str:
        del settle_seconds
        exclude = set(str(c) for c in (exclude_codes or set()))
        deadline = time.time() + timeout
        t_start = time.time()
        while time.time() < deadline:
            for m in self._fetch_mails():
                body = str(m.get("content") or m.get("text") or "")
                item = {
                    "subject": str(m.get("subject") or ""),
                    "text": body,
                    "content": body,
                }
                otp = extract_otp(item)
                if otp and str(otp) not in exclude:
                    if on_poll:
                        try:
                            on_poll({"code": str(otp), "excluded": False, "source": "cloudmail",
                                     "elapsed_s": round(time.time() - t_start, 1)})
                        except Exception:
                            pass
                    logger.info("[CloudMail] 到件 OTP=%s 延迟 %.1fs（email=%s）", otp,
                                time.time() - t_start, self.email)
                    return str(otp)
            time.sleep(interval)
        raise MailClientError(f"cloud-mail 等待 {self.email} OTP 超时（>{timeout}s）")


def generate_email(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """动态生成一个 cloud-mail 注册邮箱(不依赖号池文件)。

    从 config mail.cloud_mail.domains 随机选域名 + 随机用户名, 一邮箱一账号。
    收码由 admin 拉该地址邮件(CloudMailClient), 注册用主邮箱(不用别名)。
    """
    use_cfg = cfg or {}
    cm = (use_cfg.get("mail") or {}).get("cloud_mail") or {}
    domains = [str(d).lstrip("@") for d in (cm.get("domains") or []) if str(d).strip()]
    if not domains:
        # 无配置域名: 查 API list_domains()
        try:
            client = CloudMailClient({"email": ""}, cfg=use_cfg)
            domains = client.list_domains()
        except Exception:
            domains = []
    if not domains:
        raise MailClientError("cloud_mail 未配置可用域名 (config mail.cloud_mail.domains)")
    import secrets

    dom = secrets.choice(domains)
    user = "reg_" + secrets.token_hex(3)  # reg_xxxxxx
    email = f"{user}@{dom}"
    return {"email": email, "mail_type": "cloudmail", "raw_line": email}


class CloudMailSource(MailSource):
    """cloud-mail 号源: 号池行单段邮箱 `user@xdauv.xyz`(无 ----)。

    注册用主邮箱(不用 plus 别名)——每个 cloud-mail 邮箱独立收件, 一邮箱一账号。
    也可用 generate_email() 动态生成(不依赖号池文件)。
    """

    name = "cloudmail"

    def parse_line(self, raw: str) -> dict[str, Any] | None:
        line = raw.strip()
        if not line or "----" in line or line.startswith("#"):
            return None
        if "@" in line:
            return {"email": line, "mail_type": "cloudmail", "raw_line": line}
        return None

    def build_client(
        self, account: dict, *, proxy: str | None = None,
        impersonate: str = "chrome142", cfg: dict | None = None,
    ) -> MailClient:
        return CloudMailClient(account, cfg or {}, proxy=proxy, impersonate=impersonate)
