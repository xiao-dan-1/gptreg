"""常驻浏览器池（klsf 模式）：跨账号复用 Chrome，省 launch ~8s + 300MB/账号。

设计（解决 playwright sync 线程绑定 vs ThreadPoolExecutor 无线程亲和）:
- 浏览器只归采集线程（_Collector）创建/使用；账号线程只投递 job + 等结果，零 playwright 调用。
- 每个 _Collector = 一个采集线程 + 一个常驻 Chrome（launch 无 proxy，每账号 context 绑账号隧道口）。
- job 携带提交线程的 contextvars 快照，采集线程 ctx.run 执行 → 保持账号日志前缀。
- 失败重建：submit 超时 → mark_unhealthy + psutil 按 --user-data-dir 标记杀 Chrome → 补新采集器。
- 生命周期：惰性启动；batch 入口显式 shutdown_all() + atexit 兜底。

用法:
    from gptreg.browser_pool import get_pool, shutdown_all
    res = get_pool(cfg).submit(fn, timeout_s=70, ctx=contextvars.copy_context())
    # fn(browser) -> dict，在采集线程内执行，browser 为常驻 Chrome
    ...
    shutdown_all()
"""
from __future__ import annotations

import atexit
import contextvars
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_POOL_LOCK = threading.Lock()
_pool: "BrowserPool | None" = None
_atexit_done = False


@dataclass
class PoolJob:
    """一个待采集任务。fn 在采集线程内以 ctx 快照执行。"""

    fn: Callable[[Any], dict[str, Any]]
    result_q: "queue.Queue[dict[str, Any]]"
    ctx: contextvars.Context | None = None
    deadline: float = 0.0


def _collector_marker(index: int) -> str:
    """该采集器的进程标记（psutil 杀 Chrome 定位用）。

    Chrome 忽略未知 flag，故用自定义 flag 作进程 cmdline 标记（playwright 不允许
    --user-data-dir 走 launch args，会报错要求 launch_persistent_context）。
    """
    return f"--pw-browser-col-{index}"


class _Collector(threading.Thread):
    """一个采集线程 = 一个常驻 Chrome。daemon=True（不拖住进程退出）。"""

    def __init__(self, pool: "BrowserPool", index: int):
        super().__init__(name=f"browser-col-{index}", daemon=True)
        self.pool = pool
        self.index = index
        self._q: queue.Queue[PoolJob | None] = queue.Queue()
        self._lock = threading.Lock()
        self._browser: Any = None
        self._playwright: Any = None
        self._healthy = True
        self._served = 0
        self._max_accounts = pool._max_accounts
        self.marker = _collector_marker(index)
        self._pw_lock = threading.Lock()  # 保护 launch/close（kill 后重 launch 竞争）

    # ── 池接口 ──
    def submit(self, job: PoolJob) -> None:
        self._q.put(job)

    def stop(self) -> None:
        self._q.put(None)

    def mark_unhealthy(self) -> None:
        self._healthy = False

    def is_healthy(self) -> bool:
        return self._healthy

    def force_kill_browser(self) -> None:
        """psutil 按 cmdline 含 marker 杀 Chrome 全家（杀后卡死 sync 调用抛错返回）。"""
        import psutil

        killed = 0
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    cmd = " ".join(proc.info.get("cmdline") or [])
                    if self.marker.replace("\\", "/") in cmd.replace("\\", "/"):
                        proc.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            logger.warning("[browser-pool] 杀 Chrome 异常: %s", exc)
        if killed:
            logger.warning("[browser-pool] 已杀 %d 个 Chrome（marker col-%s）", killed, self.index)
        with self._pw_lock:
            self._browser = None
            self._playwright = None

    # ── 线程主循环 ──
    def run(self) -> None:
        logger.info("[browser-pool] 采集线程 col-%s 启动", self.index)
        while True:
            job = self._q.get()
            if job is None:
                break
            try:
                self._serve(job)
            except Exception as exc:
                logger.warning("[browser-pool] col-%s 采集异常: %s", self.index, exc)
                try:
                    job.result_q.put({"ok": False, "error": f"collector_exc: {exc}"})
                except Exception:
                    pass
            finally:
                self._q.task_done()

        # 线程退出：关浏览器
        self._close_browser()
        logger.info("[browser-pool] 采集线程 col-%s 退出", self.index)

    def _ensure_browser(self) -> Any:
        """惰性 launch；断连/重建后重 launch。返回当前 browser。"""
        with self._pw_lock:
            if self._browser is not None:
                try:
                    if self._browser.is_connected():
                        return self._browser
                except Exception:
                    pass
                # 断连：清理旧引用
                self._browser = None
                self._playwright = None
            cfg = self.pool._cfg
            browser_cfg = cfg.get("browser") or {}
            pw = self.pool._new_playwright()
            try:
                self._browser = pw.chromium.launch(
                    channel="chrome",
                    headless=bool((cfg.get("protocol") or {}).get("sentinel_browser_headless", True)),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        f"--lang={browser_cfg.get('language') or 'en-US'}",
                        self.marker,  # 自定义 flag：进程 cmdline 标记（杀 Chrome 定位用）
                    ],
                )
            except Exception:
                try:
                    pw.stop()
                except Exception:
                    pass
                raise
            self._playwright = pw
            self._served = 0
            logger.info("[browser-pool] col-%s 已启动常驻 Chrome", self.index)
            return self._browser

    def _serve(self, job: PoolJob) -> None:
        browser = self._ensure_browser()
        try:
            res = job.ctx.run(lambda: job.fn(browser)) if job.ctx else job.fn(browser)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        try:
            job.result_q.put(res)
        except Exception:
            pass
        self._served += 1
        if self._served >= self._max_accounts:
            # 单 Chrome 服务账号数到上限：重建防内存爬升
            logger.info("[browser-pool] col-%s 已达 %d 账号, 重建浏览器", self.index, self._served)
            self._close_browser()

    def _close_browser(self) -> None:
        with self._pw_lock:
            b = self._browser
            self._browser = None
            pw = self._playwright
            self._playwright = None
        if b is not None:
            try:
                b.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


class BrowserPool:
    """全局单例浏览器池。RR 选 healthy 采集器，超时隔离重建。"""

    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg
        proto = cfg.get("protocol") or {}
        self._size = max(1, int(proto.get("sentinel_browser_pool_size") or 2))
        self._timeout_s = max(10, int(proto.get("sentinel_browser_pool_timeout") or 120))
        self._max_accounts = max(1, int(proto.get("sentinel_browser_max_accounts") or 50))
        self._collectors: list[_Collector] = []
        self._lock = threading.Lock()
        self._rr = 0
        self._shutdown = False

    # ── 生命周期 ──
    def set_pool_size(self, n: int) -> None:
        n = max(1, int(n or 1))
        with self._lock:
            self._size = n
            self._ensure_started_locked()

    def ensure_started(self) -> None:
        with self._lock:
            self._ensure_started_locked()

    def _ensure_started_locked(self) -> None:
        if self._shutdown:
            return
        want = self._size
        while len(self._collectors) < want:
            col = _Collector(self, len(self._collectors))
            self._collectors.append(col)
            col.start()

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            cols = list(self._collectors)
            self._collectors = []
        for col in cols:
            try:
                col.stop()
            except Exception:
                pass
        for col in cols:
            col.join(timeout=10)
            col.force_kill_browser()

    # ── 提交 ──
    def submit(
        self,
        fn: Callable[[Any], dict[str, Any]],
        *,
        timeout_s: float,
        ctx: contextvars.Context | None = None,
    ) -> dict[str, Any]:
        """投递任务到池，等结果。超时 → 隔离采集器 + 杀 Chrome + 补替代。"""
        self.ensure_started()
        deadline = time.time() + float(timeout_s)
        result_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        while True:
            col = self._pick_healthy()
            if col is None:
                # 全部不健康：尝试补足
                with self._lock:
                    self._ensure_started_locked()
                col = self._pick_healthy()
                if col is None:
                    return {"ok": False, "error": "pool_no_healthy_collector"}

            job = PoolJob(fn=fn, result_q=result_q, ctx=ctx, deadline=deadline)
            col.submit(job)
            wait_s = max(0.1, deadline - time.time())
            try:
                res = result_q.get(timeout=wait_s)
                return res
            except queue.Empty:
                # 超时：隔离 + 杀 Chrome + 补替代，重试一次
                col.mark_unhealthy()
                col.force_kill_browser()
                logger.warning("[browser-pool] 采集超时(%.0fs)，隔离 col-%s 并重建", wait_s, col.index)
                with self._lock:
                    self._ensure_started_locked()
                # 再等一轮（新采集器冷启动）
                wait_s = max(0.1, deadline - time.time())
                if wait_s <= 0:
                    return {"ok": False, "error": "pool_timeout"}
                try:
                    res = result_q.get(timeout=wait_s)
                    return res
                except queue.Empty:
                    return {"ok": False, "error": "pool_timeout"}

    def _pick_healthy(self) -> _Collector | None:
        with self._lock:
            cols = [c for c in self._collectors if c.is_healthy()]
            if not cols:
                return None
            col = cols[self._rr % len(cols)]
            self._rr += 1
            return col

    def stats(self) -> dict[str, Any]:
        return {
            "size": len(self._collectors),
            "healthy": sum(1 for c in self._collectors if c.is_healthy()),
            "shutdown": self._shutdown,
        }

    # ── 工具 ──
    def _new_playwright(self) -> Any:
        """在采集线程内启动 playwright 运行时。"""
        from playwright.sync_api import sync_playwright

        return sync_playwright().start()


def get_pool(cfg: dict[str, Any]) -> BrowserPool:
    """模块级单例，惰性建，cfg 首次生效。"""
    global _pool
    with _POOL_LOCK:
        if _pool is None:
            _pool = BrowserPool(cfg)
            _register_atexit()
        return _pool


def shutdown_all() -> None:
    """幂等关闭全部池。batch 入口显式调用；atexit 兜底。"""
    global _pool
    with _POOL_LOCK:
        p = _pool
        _pool = None
    if p is not None:
        p.shutdown()


def _register_atexit() -> None:
    global _atexit_done
    if not _atexit_done:
        _atexit_done = True
        atexit.register(shutdown_all)
