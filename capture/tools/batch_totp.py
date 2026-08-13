#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量生产 TOTP 账号：复用 gptreg/register_pwd.register_account(结构化结果), 不再 subprocess。

主号生命周期(修复原 subprocess 把所有失败烧号的问题):
  SUCCESS         → accounts.jsonl 落盘即标记已用(下次 _unused_mains 跳过)
  MAIL_REGISTERED → 永久弃用(写 data/totp_failed.txt)——邮箱已在 OpenAI 注册
  IP_BLOCKED/基建 → 不烧号(下次批量可重试); register 400 换 IP 自愈在核心内部

用法: python capture/batch_totp.py [--limit 3] [--proxy ...] [--no-dynamic] [--list]
"""
from __future__ import annotations

import argparse
import contextvars
import json
import logging
import random
import string
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):  # 中文 logger 走 stderr, 也必须 UTF-8(否则 cp936 乱码)
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from gptreg.config import load_config, pick_password, random_birthdate, random_display_name  # noqa: E402
from gptreg.mail.pool import MailPool, parse_mail_line  # noqa: E402
from gptreg.proxyutil import proxy_label  # noqa: E402
from gptreg.register_pwd import RegisterOutcome, register_account, timing_str  # noqa: E402

OUT = ROOT / "output"
POOL = ROOT / "mail_pool.txt"
FAILED_FILE = ROOT / "data" / "totp_failed.txt"
FAIL_LOG = ROOT / "data" / "batch_failures.log"

# 批量并发: 每 worker 线程把当前账号写入 contextvar, logging Filter 注入归属前缀。
# 用 contextvars(而非 threading.local): register_pwd 的 so/t 采集子线程经
# contextvars.copy_context() 继承本 context, so 日志也能带账号前缀(threading.local 不传播)。
_account_var: contextvars.ContextVar[str] = contextvars.ContextVar("batch_account", default="")


class _AccountFilter(logging.Filter):
    """给 logger 记录注入当前账号前缀(contextvar), 并发下过程日志可归属账号。

    挂在 root handler(propagate 到 root 的所有 record 都过它), format 用 %(account)s。
    """

    def filter(self, record):
        acc = _account_var.get() or ""
        record.account = f"[{acc}] " if acc else ""
        return True


def _base(m: str) -> str:
    """主号: x+tag@dom → x@dom(去 plus tag)。"""
    e = (m or "").strip()
    if "@" not in e:
        return e
    local, dom = e.rsplit("@", 1)
    return f"{local.split('+')[0]}@{dom}"


def _used_mains() -> set[str]:
    """已用主号(accounts.jsonl 主库 + 永久弃用记录)。"""
    used: set[str] = set()
    p = OUT / "accounts.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line).get("email", "")
                used.add(_base(e))
            except Exception:
                pass
    if FAILED_FILE.exists():
        for line in FAILED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                used.add(_base(line))
    return used


def _unused_mains() -> list[tuple[str, dict]]:
    """[(主号, 号池行)]——未用过且未永久弃用的主号。

    iCloud 例外: 主号已注册仍可用一次别名(plus 别名邮件投递主邮箱收件箱, 接码 URL 能收),
    故主号在 used 里也入候选(注册时走别名), 用 accounts.jsonl 里的别名记录限制每主号 1 别名。
    """
    used = _used_mains()
    # iCloud 别名追踪: accounts.jsonl 里 icloud 别名(含 +)的 base = 已用别名的主号
    alias_bases: set[str] = set()
    try:
        acct_file = ROOT / "output" / "accounts.jsonl"
        for line in acct_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line).get("email") or ""
            except Exception:
                continue
            if "+" in e and ("@icloud.com" in e or "@me.com" in e):
                alias_bases.add(_base(e))
    except Exception:
        pass
    # 池状态文件(bad=永久弃用)合并进 used, 覆盖账号表反查盲区(iCloud 池等)
    try:
        state_file = Path(str(POOL) + ".state.json")
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8")) or {}
            for e in (state.get("used") or []):
                used.add(_base(e))
            for e in (state.get("bad") or {}):
                used.add(_base(e))
    except Exception:
        pass
    mains: list[tuple[str, dict]] = []
    for line in POOL.read_text(encoding="utf-8").splitlines():
        a = parse_mail_line(line.strip())
        if not a:
            continue
        main = _base(a["email"])
        mt = a.get("mail_type") or ""
        if mt == "icloud":
            # 已用别名的主号跳过; 否则入候选(注册时走别名)
            if main not in alias_bases:
                mains.append((main, a))
        elif main not in used:
            mains.append((main, a))
    return mains


def _alias_of(main: str) -> str:
    name, dom = main.split("@")
    tag = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"{name}+{tag}@{dom}"


def _mark_permanent(main: str) -> None:
    """永久弃用(邮箱已在 OpenAI 注册, 换 IP 无效)。"""
    FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FAILED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{main}\n")


def _log_failure(main: str, result: "RegistrationResult") -> None:
    """失败诊断落盘(data/batch_failures.log): stdout 一次性输出不留档, 复盘靠它。"""
    try:
        FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
        d = result.diag or {}
        reason = str(d.get("landing_diag") or d.get("reason") or "")
        with FAIL_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} | {main} | {result.outcome.value}"
                f" | {reason[:200]} | {timing_str(d)}\n"
            )
    except Exception:
        pass


def _is_progressed(result: "RegistrationResult") -> bool:
    """邮箱是否已推进过注册流程(状态机不可重入, 重跑必 register 400)。

    OpenAI 注册是 per-邮箱状态机: 一旦 OTP 消费/register 设密码/建号, 该邮箱不可重入。
    含所有 OTP 之后失败(so/create/session/health/enroll) + 状态冲突(mail_conflict/已注册);
    仅"未推进"失败(IP_BLOCKED/OTP_FAILED)保留可重试。
    """
    if result.outcome in (
        RegisterOutcome.MAIL_REGISTERED, RegisterOutcome.MAIL_CONFLICT,
        RegisterOutcome.SO_FAILED, RegisterOutcome.CREATE_FAILED,
        RegisterOutcome.HEALTH_FAILED, RegisterOutcome.ENROLL_FAILED,
    ):
        return True
    if result.outcome == RegisterOutcome.SESSION_FAILED:
        return bool((result.diag or {}).get("otp_got"))  # OTP 已消费则已推进
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3, help="本次批量数量(默认 3)")
    ap.add_argument("--workers", type=int, default=4, help="并发线程数(默认 4, 实测 w=4 稳定 2.75x; w=8 吞吐高但单号耗时波动)")
    ap.add_argument("--pool", default="", help="号池文件(默认 mail_pool.txt; icloud 可用 icloud_pool.txt; cloudmail=动态生成)")
    ap.add_argument("--proxy", default=None, help="固定代理(--no-dynamic 时用; 默认走 config 动态链式)")
    ap.add_argument("--no-dynamic", action="store_true", help="不用动态代理换 IP(用 --proxy 固定)")
    ap.add_argument("--list", action="store_true", help="只列出未用过主号不跑")
    ap.add_argument("-v", "--verbose", action="store_true", help="详细日志(DEBUG, 默认 INFO)")
    args = ap.parse_args()

    t_start = time.time()

    # 完整日志: 无 basicConfig 时根 logger 无 handler, register_account 的 INFO 诊断
    # (quickjs t/收码通道/enroll)全被丢弃(默认只 last-resort 显 WARNING)——恢复全可见,
    # -v 开 DEBUG 定位慢点(对齐 verify_pwd_totp)。
    # format 含 %(account)s: 并发 worker 的 INFO 经 _AccountFilter 注入当前账号前缀可归属。
    # 须挂 root handler(Handler.filter 对每个进 handler 的 record 生效)——logger 级 filter
    # 只对发出点 logger 自身生效, 不覆盖 propagate 上来的子 logger record(会 KeyError: account)。
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(account)s%(message)s")
    for _h in logging.getLogger().handlers:
        _h.addFilter(_AccountFilter())

    cfg = load_config()
    # CloudMail: 动态生成邮箱(不依赖号池文件)
    if args.pool == "cloudmail":
        from gptreg.mail.cloudmail import generate_email

        batch = []
        for _ in range(args.limit):
            acct = generate_email(cfg)
            batch.append((acct["email"], acct))
        print(f"CloudMail 动态生成 {len(batch)} 个邮箱")
        if args.list:
            for main, _ in batch:
                print("  ", main)
            return 0
        proxy = None if not args.no_dynamic else args.proxy
        rc = _run_batch(batch, proxy, cfg, workers=args.workers)
        print(f"\n[总耗时] {(time.time()-t_start):.1f}s", flush=True)
        return rc

    # 号池文件(默认 mail_pool.txt; --pool 支持 icloud 快捷名)
    pool_file = args.pool or "mail_pool.txt"
    if pool_file == "icloud":
        pool_file = "icloud_pool.txt"
    global POOL
    POOL = ROOT / pool_file

    # 号池状态机(批量后标记 used/bad, 与账号表同步避免坏号反复试)
    pool = MailPool(pool_file, accounts_jsonl=str(ROOT / "output" / "accounts.jsonl"))
    pool.load()

    unused = _unused_mains()
    print(f"未用过主号: {len(unused)} 个")
    if args.list:
        for main, _ in unused[: args.limit or None]:
            print("  ", main)
        return 0

    proxy = None if not args.no_dynamic else args.proxy
    if proxy:
        print(f"固定代理: {proxy}")
    else:
        print("动态代理: 每次换 sid(新出口), register 400 自动换 IP 重试")

    batch = unused[: args.limit]
    rc = _run_batch(batch, proxy, cfg, pool, workers=args.workers)
    print(f"\n[总耗时] {(time.time()-t_start):.1f}s", flush=True)
    return rc


def _run_batch(batch: list[tuple[str, dict]], proxy, cfg, pool=None, workers: int = 1) -> int:
    """批量注册: batch=[(主号, account)]。pool 为 MailPool(批量后标记 used/bad)。

    workers>1 用线程池并发(参考 turb-gpt-free-register 的 --workers 设计):
      I/O 密集(等收码/网络), 多线程有效; 每账号独立隧道/浏览器, 无共享可变状态;
      号池/落盘已有锁(线程安全)。建议 workers ≤ 可用代理/IP 数(避免共用 IP 风控)。
    """
    # (idx, main, ok, outcome, dt_s) —— dt 供串行预估/加速比
    results: list[tuple[int, str, bool, RegisterOutcome, float]] = []
    workers = max(1, int(workers or 1))
    _batch_t0 = time.time()

    def _one_job(idx: int, main: str, account: dict) -> tuple[int, str, bool, RegisterOutcome, float]:
        # Outlook(ms_oauth) + iCloud 用别名; cloudmail/api 用主邮箱
        # (iCloud plus 别名邮件投递主邮箱收件箱, 接码 URL 能收; cloudmail/api 按主号拉码)
        if account.get("mail_type") in ("ms_oauth", "icloud"):
            email = _alias_of(main)
        else:
            email = main
        password = pick_password(cfg)  # 统一密码(config)或随机——半注册邮箱可用统一密码找回
        display_name = random_display_name()
        bday = random_birthdate(cfg)
        print(f"\n[{idx + 1}/{len(batch)}] 主号 {main} ...", flush=True)
        t0 = time.time()
        _tok = _account_var.set(main)  # logging 归属前缀(contextvar, 子线程经 copy_context 继承)
        try:
            result = _register_with_retry(
                cfg, account, email, password, display_name, bday, proxy,
                proxy_pool=proxy_pool,
            )
        finally:
            _account_var.reset(_tok)
        dt = time.time() - t0
        ok = result.outcome == RegisterOutcome.SUCCESS
        print(f"  注册邮箱: {email}  身份: {display_name}", flush=True)
        if ok:
            print(f"  -> 成功 ({dt:.0f}s) TOTP: {((result.record or {}).get('totp_secret') or '')[:12]}...", flush=True)
            pu = (result.record or {}).get("proxy_used") or ""
            if pu:
                print(f"  [出口] {proxy_label(pu)}", flush=True)
        else:
            d = result.diag or {}
            # IP_BLOCKED / MAIL_CONFLICT: 落点/状态诊断(专门文案) + 服务器原始 code/reason
            if result.outcome in (RegisterOutcome.IP_BLOCKED, RegisterOutcome.MAIL_CONFLICT):
                ld = str(d.get("landing_diag") or d.get("reason") or "")[:120]
                print(f"  [{result.outcome.value}] {ld}", flush=True)
                if d.get("srv_code"):
                    print(f"  [register/服务器] code={d.get('srv_code')} redirect={str(d.get('srv_redirect',''))[:60]}", flush=True)
                elif d.get("reason"):
                    # srv_code 空时补服务器原文(如 "Invalid authorization", 比 landing_diag 更精确)
                    print(f"  [register/服务器] {str(d.get('reason'))[:120]}", flush=True)
            else:
                reason = str(d.get("landing_diag") or d.get("reason") or "")[:150]
                print(f"  [{result.outcome.value}] {reason}", flush=True)
            print(f"  -> 失败 ({dt:.0f}s)", flush=True)
            _log_failure(main, result)
        ts = timing_str(result.diag)
        if ts:
            print(f"  [耗时] {ts}", flush=True)
        # 主号生命周期: 与号池 state 同步(避免坏号反复试); 锁内线程安全。
        # 已推进注册流程的邮箱不可重跑(状态机不可重入, 重跑必 register 400, 换 IP 无效且
        # 放大认证请求触发 rate_limit) → 一律弃用; 仅"未推进"失败(IP_BLOCKED/OTP_FAILED)保留。
        if result.outcome == RegisterOutcome.SUCCESS and pool is not None:
            pool.mark_used(main)
        elif pool is not None and _is_progressed(result):
            if result.outcome == RegisterOutcome.MAIL_REGISTERED:
                _mark_permanent(main)
                print(f"  [永久弃用] 邮箱已注册, 已记 {FAILED_FILE.name} + 号池 bad", flush=True)
            else:
                pool.mark_bad(main, reason=f"{result.outcome.value} 已推进注册")
                print(f"  [弃用] {result.outcome.value}: 邮箱已推进注册流程(重跑必 400), 已记号池 bad", flush=True)
        # 失败冷却: 避免快速连发(尤其 rate_limit 触发后), 降低认证请求频率
        time.sleep(4 if not ok else 1)
        return idx, main, ok, result.outcome, dt

    # 常驻浏览器池(klsf)生命周期：reuse 开时按 workers 设池大小(上限 config 控制)；批量结束关池
    from gptreg.browser_pool import get_pool, shutdown_all

    if bool(((cfg.get("protocol") or {}).get("sentinel_browser_reuse"))):
        try:
            get_pool(cfg).set_pool_size(min(workers, max(1, int((cfg.get("protocol") or {}).get("sentinel_browser_pool_size") or 2))))
        except Exception as exc:
            print(f"[浏览器池] 设池大小失败: {exc}", flush=True)
    # 动态代理池(纯协议正解): 预建探活过的隧道, 并发各取一条(免每号现场建隧道+探活,
    # 坏隧道池自愈 discard 换新)。仅并发+非固定代理启用; 串行/固定代理走 resolve_proxy 兼容旧路径。
    proxy_pool = None
    if workers > 1 and not proxy:
        try:
            from gptreg.proxyutil import ProxyPool
            _pp_size = int(((cfg.get("proxy") or {}).get("dynamic") or {}).get("pool_size") or 8)
            proxy_pool = ProxyPool(cfg, size=min(workers * 2, _pp_size))
            print(f"[代理池] 预建 {proxy_pool.size()} 条隧道(并发 {workers})", flush=True)
        except Exception as exc:
            print(f"[代理池] 建池失败(回退每号现场建隧道): {exc}", flush=True)
            proxy_pool = None
    try:
        if workers <= 1:
            for i, (main, account) in enumerate(batch):
                results.append(_one_job(i, main, account))
        else:
            from concurrent.futures import ThreadPoolExecutor
            print(f"并发注册: {workers} 线程", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_one_job, i, main, account): i
                           for i, (main, account) in enumerate(batch)}
                for f in futures:
                    results.append(f.result())
    finally:
        if proxy_pool is not None:
            try:
                proxy_pool.close()
            except Exception:
                pass
        shutdown_all()

    results.sort(key=lambda r: r[0])  # 按提交顺序
    n_ok = sum(1 for _, _, ok, _, _ in results if ok)
    batch_t = time.time() - _batch_t0
    print(f"\n批量完成: {n_ok}/{len(batch)} 成功", flush=True)
    for _, main, ok, oc, _dt in results:
        print(f"  [{'OK' if ok else 'X'}] {main} ({oc.value})", flush=True)
    # 吞吐基线: 串行预估(Σ 单号耗时) vs 实际批量耗时 → 并行加速比(workers 效率)
    if len(results) > 1 and batch_t > 0:
        serial = sum(_dt for _, _, _, _, _dt in results)
        print(f"批量耗时 {batch_t:.1f}s | 串行预估 {serial:.0f}s | 加速比 {serial/batch_t:.2f}x ({workers} 线程)", flush=True)
    return 0 if n_ok == len(batch) else 1


def _register_with_retry(cfg, account, email, password, name, bday, proxy, proxy_pool=None):
    """注册 + IP_BLOCKED 当轮重试 1 次(换 IP 大概率成, 避免直接弃下一轮)。"""
    result = register_account(cfg, account, email=email, password=password,
                              name=name, bday=bday, proxy=proxy, proxy_pool=proxy_pool)
    if result.outcome == RegisterOutcome.IP_BLOCKED:
        # email-verification 邮箱级风控: 同邮箱换 IP 无效(实测换 3-5 IP 全失败), 不重试
        if (result.diag or {}).get("email_verification_required"):
            print("  [ip_blocked] email-verification(邮箱级风控), 不重试(换 IP 无效)", flush=True)
        else:
            # 换 sid 重试一次: register_account 内部已换 sid(池模式=换池隧道), 重试是让 IP 风控概率解
            print("  [retry] IP_BLOCKED, 换 IP 重试一次", flush=True)
            result = register_account(cfg, account, email=email, password=password,
                                      name=name, bday=bday, proxy=proxy, proxy_pool=proxy_pool)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
