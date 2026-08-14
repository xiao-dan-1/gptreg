#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const { performance } = require("node:perf_hooks");

// auth.openai.com / sentinel.openai.com 国内被墙，node fetch 经代理（SENTINEL_PROXY，如 http://127.0.0.1:7890）
try {
  const _proxy = process.env.SENTINEL_PROXY;
  if (_proxy) {
    const { ProxyAgent, setGlobalDispatcher } = require("undici");
    setGlobalDispatcher(new ProxyAgent(_proxy));
  }
} catch (_e) { /* undici 不可用则忽略 */ }

function readArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = "1";
      continue;
    }
    args[key] = next;
    i++;
  }
  return args;
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseJson(text, source) {
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${source} 不是合法 JSON：${error.message}`);
  }
}

function pick(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function truthy(value) {
  return value === true || value === "1" || value === "true" || value === "yes";
}

function readConfig(args) {
  const explicitPath = args.config || process.env.SENTINEL_CONFIG;
  const candidates = explicitPath
    ? [path.resolve(explicitPath)]
    : [
        path.resolve(process.cwd(), "sentinel.config.json"),
        path.resolve(process.cwd(), "tools", "sentinel.config.json"),
        path.resolve(__dirname, "sentinel.config.json"),
        path.resolve(__dirname, "..", "sentinel.config.json"),
      ];

  for (const filePath of candidates) {
    if (!fs.existsSync(filePath)) continue;
    return {
      path: filePath,
      data: parseJson(fs.readFileSync(filePath, "utf8"), filePath),
    };
  }

  return { path: null, data: {} };
}

function configGetter(config) {
  return (...keys) => {
    for (const key of keys) {
      if (config[key] !== undefined && config[key] !== null && config[key] !== "") {
        return config[key];
      }
    }
    return "";
  };
}

function normalizeList(value, fallback) {
  const source = Array.isArray(value) ? value.join(",") : pick(value, fallback);
  return String(source)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function xorDecode(text, key) {
  let output = "";
  const decoded = atobBinary(text);
  for (let i = 0; i < decoded.length; i++) {
    output += String.fromCharCode(decoded.charCodeAt(i) ^ key.charCodeAt(i % key.length));
  }
  return output;
}

function decodeDx(dx, proof) {
  return JSON.parse(xorDecode(dx, proof));
}

function normalizeChallenge(raw) {
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return trimmed;
    raw = parseJson(trimmed, "challenge 字符串");
  }

  const candidates = [
    raw?.cachedChatReq,
    raw?.result?.cachedChatReq,
    raw?.data?.cachedChatReq,
    raw?.data,
    raw,
  ];

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue;
    if (candidate.proofofwork || candidate.token || candidate.turnstile || candidate.so) {
      return candidate;
    }
  }

  throw new Error("challenge 缺少 cachedChatReq/proofofwork/token 字段，无法喂给 SDK");
}

function readChallengeFile(filePath) {
  const absolutePath = path.resolve(filePath);
  const raw = fs.readFileSync(absolutePath, "utf8");
  return normalizeChallenge(parseJson(raw, absolutePath));
}

const OFFICIAL_CHALLENGE_URL = "https://chatgpt.com/backend-api/sentinel/req";

function headerMapFromEnv(options = {}) {
  const headers = {
    accept: "*/*",
    "content-type":
      options.contentType ||
      (options.ignoreEnv ? "" : process.env.SENTINEL_CONTENT_TYPE) ||
      "text/plain;charset=UTF-8",
  };
  const cookie =
    options.cookie ||
    (options.ignoreEnv ? "" : process.env.SENTINEL_COOKIE || process.env.CHATGPT_COOKIE);
  const authorization =
    options.bearer ||
    (options.ignoreEnv ? "" : process.env.SENTINEL_AUTHORIZATION || process.env.CHATGPT_BEARER_TOKEN);
  const userAgent = options.userAgent || (options.ignoreEnv ? "" : process.env.SENTINEL_USER_AGENT);

  if (cookie) headers.cookie = cookie;
  if (authorization) {
    headers.authorization = authorization.toLowerCase().startsWith("bearer ")
      ? authorization
      : `Bearer ${authorization}`;
  }
  if (userAgent) {
    headers["user-agent"] = userAgent;
  }
  if (options.pageUrl) headers.referer = options.pageUrl;
  if (options.origin) headers.origin = options.origin;
  if (options.deviceId) headers["oai-device-id"] = options.deviceId;
  if (process.env.SENTINEL_HEADERS_JSON) {
    Object.assign(headers, parseJson(process.env.SENTINEL_HEADERS_JSON, "SENTINEL_HEADERS_JSON"));
  }
  return headers;
}

function assertAllowedChallengeHost(challengeUrl, officialMode) {
  const host = new URL(challengeUrl).hostname.toLowerCase();
  const allowed = (process.env.SENTINEL_ALLOW_HOST || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);

  if ((host === "chatgpt.com" || host.endsWith(".chatgpt.com")) && !officialMode && !allowed.includes(host)) {
    throw new Error(
      "为避免误打真实生产接口，默认不请求 chatgpt.com。若这是比赛授权接口，请使用 --official 或设置 SENTINEL_ALLOW_HOST=chatgpt.com。"
    );
  }
}

async function fetchChallenge(challengeUrl, flow, proof, deviceId, options = {}) {
  assertAllowedChallengeHost(challengeUrl, options.officialMode);
  const hasCookie = Boolean(
    options.cookie || (options.ignoreEnv ? "" : process.env.SENTINEL_COOKIE || process.env.CHATGPT_COOKIE)
  );
  const hasBearer = Boolean(
    options.bearer ||
      (options.ignoreEnv ? "" : process.env.SENTINEL_AUTHORIZATION || process.env.CHATGPT_BEARER_TOKEN)
  );
  if (options.officialMode && !hasCookie && !hasBearer) {
    throw new Error("官方接口模式至少需要 Cookie 或 Bearer；请传 --cookie 或 --bearer。");
  }
  const body = JSON.stringify({ p: proof, id: deviceId, flow });
  const response = await fetch(challengeUrl, {
    method: "POST",
    headers: headerMapFromEnv({
      pageUrl: options.pageUrl,
      origin: new URL(challengeUrl).origin,
      userAgent: options.userAgent,
      deviceId,
      cookie: options.cookie,
      bearer: options.bearer,
      contentType: options.contentType,
      ignoreEnv: options.ignoreEnv,
    }),
    body,
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`challenge API 返回 HTTP ${response.status}：${text.slice(0, 300)}`);
  }
  return normalizeChallenge(text);
}

function createEventTarget() {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      bucket.push(listener);
      listeners.set(type, bucket);
    },
    removeEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      listeners.set(
        type,
        bucket.filter((item) => item !== listener)
      );
    },
    dispatchEvent(event) {
      const bucket = listeners.get(event.type) || [];
      for (const listener of [...bucket]) listener.call(this, event);
    },
  };
}

function btoaBinary(value) {
  return Buffer.from(String(value), "binary").toString("base64");
}

function atobBinary(value) {
  return Buffer.from(String(value), "base64").toString("binary");
}

function createStorage() {
  const values = new Map();
  return {
    get length() {
      return values.size;
    },
    key(index) {
      return [...values.keys()][Number(index)] ?? null;
    },
    getItem(key) {
      const name = String(key);
      return values.has(name) ? values.get(name) : null;
    },
    setItem(key, value) {
      values.set(String(key), String(value));
    },
    removeItem(key) {
      values.delete(String(key));
    },
    clear() {
      values.clear();
    },
  };
}

function createDomRect(width = 0, height = 0) {
  return {
    x: 0,
    y: 0,
    width,
    height,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    toJSON() {
      return {
        x: this.x,
        y: this.y,
        width: this.width,
        height: this.height,
        top: this.top,
        left: this.left,
        right: this.right,
        bottom: this.bottom,
      };
    },
  };
}

// ★P0-2 时区伪造:沙箱原样透传 Node 宿主机的真实 Date/Intl 会暴露宿主机所在时区
// (跑脚本这台机器是 Asia/Shanghai),跟代理伪装的美区 IP 地理位置矛盾——这种"设备时区
// vs IP 地理位置"不一致是通用反欺诈的基础校验项之一,且这类校验常是异步/批量跑的,
// 不会当场拦截,能解释账号注册成功后隔一段时间才被回收的现象。
// 这里让沙箱内的 Date/Intl.DateTimeFormat 都按 options.timezone(IANA 时区名,
// 应该跟当前用的代理出口地理位置对应)伪造,而不是用真实宿主机时区。
function getTimezoneOffsetMinutes(tzName, instant) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tzName, hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const p = {};
  for (const part of dtf.formatToParts(instant)) p[part.type] = part.value;
  const asUTC = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day),
                        Number(p.hour), Number(p.minute), Number(p.second));
  return Math.round((instant.getTime() - asUTC) / 60000);
}

function formatGmtOffset(offsetMinutes) {
  // getTimezoneOffset() 符号约定与常见 "GMT+/-HHMM" 字符串相反(西半球时区 offset 为正)
  const sign = offsetMinutes > 0 ? "-" : "+";
  const abs = Math.abs(offsetMinutes);
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  return `GMT${sign}${hh}${mm}`;
}

function getZoneDisplayName(tzName, instant) {
  const part = new Intl.DateTimeFormat("en-US", { timeZone: tzName, timeZoneName: "long" })
    .formatToParts(instant).find((p) => p.type === "timeZoneName");
  return part ? part.value : tzName;
}

function getWallClockParts(tzName, instant) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tzName, hourCycle: "h23", weekday: "short",
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const p = {};
  for (const part of dtf.formatToParts(instant)) p[part.type] = part.value;
  return p;
}

function buildSpoofedDate(tzName) {
  return class SpoofedDate extends Date {
    getTimezoneOffset() {
      return getTimezoneOffsetMinutes(tzName, this);
    }
    toString() {
      const p = getWallClockParts(tzName, this);
      const offset = getTimezoneOffsetMinutes(tzName, this);
      return `${p.weekday} ${p.month} ${p.day} ${p.year} ${p.hour}:${p.minute}:${p.second} `
           + `${formatGmtOffset(offset)} (${getZoneDisplayName(tzName, this)})`;
    }
    toDateString() {
      const p = getWallClockParts(tzName, this);
      return `${p.weekday} ${p.month} ${p.day} ${p.year}`;
    }
    toTimeString() {
      const p = getWallClockParts(tzName, this);
      const offset = getTimezoneOffsetMinutes(tzName, this);
      return `${p.hour}:${p.minute}:${p.second} ${formatGmtOffset(offset)} (${getZoneDisplayName(tzName, this)})`;
    }
    toLocaleString(locale, opts) {
      return new Intl.DateTimeFormat(locale || "en-US", { timeZone: tzName, ...(opts || {}) }).format(this);
    }
    toLocaleDateString(locale, opts) {
      return new Intl.DateTimeFormat(locale || "en-US",
        { timeZone: tzName, year: "numeric", month: "numeric", day: "numeric", ...(opts || {}) }).format(this);
    }
    toLocaleTimeString(locale, opts) {
      return new Intl.DateTimeFormat(locale || "en-US",
        { timeZone: tzName, hour: "numeric", minute: "numeric", second: "numeric", ...(opts || {}) }).format(this);
    }
  };
}

function buildSpoofedIntl(tzName) {
  class SpoofedDateTimeFormat extends Intl.DateTimeFormat {
    constructor(locales, options) {
      const opts = options ? { ...options } : {};
      if (!opts.timeZone) opts.timeZone = tzName;
      super(locales, opts);
    }
  }
  // 原型链继续指向真实 Intl,Collator/NumberFormat 等其余部分原样可用,只覆盖 DateTimeFormat
  const spoofed = Object.create(Intl);
  spoofed.DateTimeFormat = SpoofedDateTimeFormat;
  return spoofed;
}

function createBrowserContext(options) {
  const windowTarget = createEventTarget();
  const managedTimers = new Set();
  const managedSetTimeout = (callback, delay, ...args) => {
    const id = setTimeout(() => {
      managedTimers.delete(id);
      callback(...args);
    }, delay);
    managedTimers.add(id);
    return id;
  };
  const managedClearTimeout = (id) => {
    managedTimers.delete(id);
    clearTimeout(id);
  };
  const browserPerformance = {
    now: () => performance.now(),
    timeOrigin: performance.timeOrigin || Date.now() - performance.now(),
    memory: {
      jsHeapSizeLimit: options.jsHeapSizeLimit,
    },
  };
  const mathObject = Object.create(Math);
  if (Number.isFinite(options.fixedRandom)) {
    mathObject.random = () => options.fixedRandom;
  }
  const timezoneName = options.timezone || "America/Los_Angeles";
  const SpoofedDate = buildSpoofedDate(timezoneName);
  const spoofedIntl = buildSpoofedIntl(timezoneName);
  const currentScript = { src: options.scriptSrc, length: options.scriptSrc.length };
  const scripts = [
    currentScript,
    { src: "https://js.stripe.com/v3/", length: 24 },
    { src: "https://chatgpt.com/c/prod-4987068829830ddc3ae6683bd4e633f61b79dec9/_ssg.js", length: 82 },
  ];
  const attrs = new Map();
  if (options.buildId) attrs.set("data-build", options.buildId);

  let iframeNode = null;
  const bodyChildren = [];
  const document = {
    currentScript,
    scripts,
    cookie: options.cookie,
    documentElement: {
      getAttribute(name) {
        return attrs.get(name) ?? null;
      },
      setAttribute(name, value) {
        attrs.set(name, String(value));
      },
    },
    body: {
      style: {},
      getBoundingClientRect() {
        return createDomRect(options.screen.width, options.screen.height);
      },
      appendChild(node) {
        bodyChildren.push(node);
        node.parentNode = document.body;
        if (node?.tagName === "IFRAME") iframeNode = node;
        managedSetTimeout(() => node?._emitLoad?.(), 0);
        return node;
      },
      removeChild(node) {
        const index = bodyChildren.indexOf(node);
        if (index >= 0) bodyChildren.splice(index, 1);
        if (iframeNode === node) iframeNode = null;
        if (node) node.parentNode = null;
        return node;
      },
    },
    createElement(tagName) {
      if (String(tagName).toLowerCase() !== "iframe") {
        const children = [];
        const element = {
          tagName: String(tagName).toUpperCase(),
          style: {},
          parentNode: null,
          children,
          appendChild(node) {
            children.push(node);
            node.parentNode = element;
            return node;
          },
          removeChild(node) {
            const index = children.indexOf(node);
            if (index >= 0) children.splice(index, 1);
            if (node) node.parentNode = null;
            return node;
          },
          addEventListener() {},
          removeEventListener() {},
          getBoundingClientRect() {
            return createDomRect();
          },
        };
        return element;
      }

      const target = createEventTarget();
      const iframe = {
        tagName: "IFRAME",
        style: {},
        src: "",
        getBoundingClientRect() {
          return createDomRect();
        },
        contentWindow: {
          postMessage(message, origin) {
            Promise.resolve()
              .then(async () => {
                const result = await options.handleIframeMessage(message);
                windowTarget.dispatchEvent({
                  type: "message",
                  source: iframe.contentWindow,
                  origin,
                  data: {
                    type: "response",
                    requestId: message.requestId,
                    result,
                  },
                });
              })
              .catch((error) => {
                windowTarget.dispatchEvent({
                  type: "message",
                  source: iframe.contentWindow,
                  origin,
                  data: {
                    type: "response",
                    requestId: message.requestId,
                    error: error?.message || String(error),
                  },
                });
              });
          },
        },
        addEventListener: target.addEventListener,
        removeEventListener: target.removeEventListener,
        _emitLoad() {
          target.dispatchEvent.call(iframe, { type: "load", target: iframe });
        },
      };
      return iframe;
    },
  };

  const location = new URL(options.pageUrl);
  const navigator = {
    userAgent: options.userAgent,
    language: options.language,
    languages: options.languages,
    hardwareConcurrency: options.hardwareConcurrency,
    bluetooth: { toString: () => "[object Bluetooth]" },
  };
  const localStorage = createStorage();
  const sessionStorage = createStorage();
  const history = {
    length: 1,
    state: null,
    back() {},
    forward() {},
    go() {},
    pushState(state) {
      this.state = state ?? null;
    },
    replaceState(state) {
      this.state = state ?? null;
    },
  };

  const window = Object.assign(windowTarget, {
    window: null,
    self: null,
    top: null,
    parent: null,
    document,
    navigator,
    screen: options.screen,
    location,
    localStorage,
    sessionStorage,
    history,
    performance: browserPerformance,
    crypto: crypto.webcrypto,
    TextEncoder,
    TextDecoder,
    URL,
    URLSearchParams,
    AbortController,
    setTimeout: managedSetTimeout,
    clearTimeout: managedClearTimeout,
    btoa: btoaBinary,
    atob: atobBinary,
    fetch,
    console,
    Math: mathObject,
    Date: SpoofedDate,
    Intl: spoofedIntl,
    JSON,
    Array,
    Object,
    Reflect,
    Number,
    String,
    Promise,
    RegExp,
    Error,
    Map,
    Set,
    WeakMap,
    Uint8Array,
    encodeURIComponent,
    decodeURIComponent,
    unescape,
    requestIdleCallback(callback) {
      return managedSetTimeout(() => callback({ timeRemaining: () => 5, didTimeout: false }), 0);
    },
    cancelIdleCallback(id) {
      managedClearTimeout(id);
    },
    __privateStripeFrame8094: {},
    onpageswap: null,
  });

  window.window = window;
  window.self = window;
  window.top = window;
  window.parent = window;

  return {
    iframeNode: () => iframeNode,
    context: vm.createContext({
      window,
      self: window,
      globalThis: window,
      document,
      navigator,
      screen: options.screen,
      location,
      localStorage,
      sessionStorage,
      history,
      performance: browserPerformance,
      crypto: crypto.webcrypto,
      TextEncoder,
      TextDecoder,
      URL,
      URLSearchParams,
      AbortController,
      setTimeout: managedSetTimeout,
      clearTimeout: managedClearTimeout,
      btoa: btoaBinary,
      atob: atobBinary,
      fetch,
      console,
      Math: mathObject,
      Date: SpoofedDate,
      Intl: spoofedIntl,
      JSON,
      Array,
      Object,
      Reflect,
      Number,
      String,
      Promise,
      RegExp,
      Error,
      Map,
      Set,
      WeakMap,
      Uint8Array,
      encodeURIComponent,
      decodeURIComponent,
      unescape,
      requestIdleCallback: window.requestIdleCallback,
      cancelIdleCallback: window.cancelIdleCallback,
      __privateStripeFrame8094: window.__privateStripeFrame8094,
      onpageswap: window.onpageswap,
    }),
    clearTimers() {
      for (const id of [...managedTimers]) managedClearTimeout(id);
    },
  };
}

async function main(argv = process.argv.slice(2), writeOutput = true) {
  const args = readArgs(argv);
  if (args.help === "1" || args.h === "1") {
    const helpText = [
      "用法：",
      "  node sentinel-runner.js --cookie \"你的 Cookie\"",
      "  node sentinel-runner.js --bearer \"Bearer 你的 token\"",
      "  node sentinel-runner.js --cookie \"你的 Cookie\" --bearer \"Bearer 你的 token\"",
      "  node sentinel-runner.js --config sentinel.config.json",
      "",
      "默认会读取当前目录、tools 目录或项目根目录的 sentinel.config.json。",
      "",
      "常用参数：",
      "  --flow checkout_session_approval",
      "  --page-url https://chatgpt.com/checkout/openai_llc/cs_xxx",
      "  --device-id 你的_oai-did",
      "  --challenge-url 自定义题目 challenge API",
      "  --sdk 指定 sdk.js 路径",
      "  --no-cookie 生成 token 时不向 challenge API 发送 Cookie",
    ].join("\n");
    if (writeOutput) process.stdout.write(`${helpText}\n`);
    return helpText;
  }

  const { path: configPath, data: config } = readConfig(args);
  const ignoreEnvForCredentials = Boolean(configPath);
  const cfg = configGetter(config);
  const defaultSdkPath = fs.existsSync(path.resolve(__dirname, "sdk.js"))
    ? path.resolve(__dirname, "sdk.js")
    : path.resolve(__dirname, "..", "sdk.js");
  const sdkPath = path.resolve(pick(args["sdk"], cfg("sdk", "sdkPath"), process.env.SENTINEL_SDK_PATH, defaultSdkPath));
  const flow = pick(args.flow, cfg("flow"), process.env.SENTINEL_FLOW, "checkout_session_approval");
  const challengeFile = pick(args["challenge-file"], cfg("challengeFile", "challenge_file"), process.env.SENTINEL_CHALLENGE_FILE);
  const officialMode =
    args.official === "1" ||
    truthy(cfg("official")) ||
    process.env.SENTINEL_OFFICIAL === "1" ||
    (!challengeFile && !args["challenge-url"] && !cfg("challengeUrl", "challenge_url") && !process.env.SENTINEL_CHALLENGE_URL);
  const challengeUrl =
    pick(args["challenge-url"], cfg("challengeUrl", "challenge_url"), process.env.SENTINEL_CHALLENGE_URL) ||
    (officialMode ? OFFICIAL_CHALLENGE_URL : "");
  const noCookie = args["no-cookie"] === "1" || truthy(cfg("noCookie", "no_cookie"));
  const cookieArg = noCookie ? "" : pick(args.cookie, args.cookies, cfg("cookie", "cookies"));
  const bearerArg = pick(args.bearer, args.authorization, cfg("bearer", "bearerToken", "authorization", "accessToken"));
  const contentType = pick(args["content-type"], cfg("contentType", "content_type"));
  const debugDx = args["debug-dx"] === "1" || truthy(cfg("debugDx", "debug_dx"));
  const debugDxLimit = Number(pick(args["debug-dx-limit"], cfg("debugDxLimit", "debug_dx_limit"), 80));
  const deviceId =
    pick(args["device-id"], cfg("deviceId", "device_id", "oaiDid", "oai_did"), process.env.SENTINEL_OAI_DID) ||
    "8a5ad769-e9e7-4461-ae3a-6755d7f46b0b";

  if (!fs.existsSync(sdkPath)) throw new Error(`找不到 SDK 文件：${sdkPath}`);
  if (!challengeFile && !challengeUrl) {
    throw new Error("请提供 --challenge-file、--challenge-url 或 --official，用于把题目服务器 challenge 喂回 SDK。");
  }

  let cachedChallenge = null;
  const options = {
    flow,
    pageUrl: pick(args["page-url"], cfg("pageUrl", "page_url"), process.env.SENTINEL_PAGE_URL, "https://chatgpt.com/checkout/openai_llc/cs_ctf"),
    scriptSrc:
      pick(
        args["script-src"],
        cfg("scriptSrc", "script_src"),
        process.env.SENTINEL_SCRIPT_SRC,
      "https://chatgpt.com/sentinel/20260423af3c/sdk.js",
      ),
    buildId: (args["no-build-id"] === "1" || truthy(cfg("noBuildId", "no_build_id")))
      ? ""
      : pick(args["build-id"], cfg("buildId", "build_id"), process.env.SENTINEL_BUILD_ID, "prod-4987068829830ddc3ae6683bd4e633f61b79dec9"),
    cookie: noCookie
      ? `oai-did=${deviceId}`
      : cookieArg ||
        (ignoreEnvForCredentials ? "" : process.env.SENTINEL_COOKIE || process.env.CHATGPT_COOKIE) ||
        `oai-did=${deviceId}`,
    userAgent:
      pick(
        args["user-agent"],
        cfg("userAgent", "user_agent"),
        process.env.SENTINEL_USER_AGENT,
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
      ),
    contentType,
    language: pick(args.language, cfg("language"), process.env.SENTINEL_LANGUAGE, "zh-CN"),
    languages: normalizeList(pick(args.languages, cfg("languages")), process.env.SENTINEL_LANGUAGES || "zh-CN,en,en-GB,en-US"),
    // ★P0-2:沙箱内 Date/Intl 按这个 IANA 时区名伪造,应该跟当前用的代理出口地理位置对应
    // (不设置则默认 America/Los_Angeles,对应默认的加州代理段)。
    timezone: pick(args.timezone, cfg("timezone"), process.env.SENTINEL_TIMEZONE, "America/Los_Angeles"),
    hardwareConcurrency: Number(pick(args.cores, cfg("cores", "hardwareConcurrency"), process.env.SENTINEL_CORES, 32)),
    jsHeapSizeLimit: Number(pick(args["js-heap-size-limit"], cfg("jsHeapSizeLimit", "js_heap_size_limit"), process.env.SENTINEL_JS_HEAP_SIZE_LIMIT, 4294967296)),
    fixedRandom:
      pick(args.random, cfg("random", "fixedRandom"), process.env.SENTINEL_FIXED_RANDOM)
        ? Number(pick(args.random, cfg("random", "fixedRandom"), process.env.SENTINEL_FIXED_RANDOM))
        : Number.NaN,
    screen: {
      width: Number(pick(args.width, cfg("width", "screenWidth"), process.env.SENTINEL_SCREEN_WIDTH, 2560)),
      height: Number(pick(args.height, cfg("height", "screenHeight"), process.env.SENTINEL_SCREEN_HEIGHT, 1440)),
    },
    async handleIframeMessage(message) {
      if (message.type !== "token" && message.type !== "init") {
        throw new Error(`未知 iframe 消息类型：${message.type}`);
      }
      const proof = message.p;
      if (challengeFile) {
        cachedChallenge ||= readChallengeFile(challengeFile);
      } else {
        cachedChallenge = await fetchChallenge(challengeUrl, flow, proof, deviceId, {
          officialMode,
          pageUrl: options.pageUrl,
          userAgent: options.userAgent,
          cookie: noCookie ? "" : cookieArg,
          bearer: bearerArg,
          contentType: options.contentType,
          ignoreEnv: ignoreEnvForCredentials,
        });
      }
      if (debugDx && cachedChallenge?.turnstile?.dx) {
        try {
          const decoded = decodeDx(cachedChallenge.turnstile.dx, proof);
          const limit = Number.isFinite(debugDxLimit) && debugDxLimit > 0 ? debugDxLimit : 80;
          process.stderr.write(`dx 前 ${limit} 条指令：${JSON.stringify(decoded.slice(0, limit))}\n`);
        } catch (error) {
          process.stderr.write(`dx 解码失败：${error.message}\n`);
        }
      }
      return {
        cachedProof: proof,
        cachedChatReq: cachedChallenge,
      };
    },
  };

  const { context, clearTimers } = createBrowserContext(options);
  let sdkCode = fs.readFileSync(sdkPath, "utf8");
  if (debugDx) {
    sdkCode = sdkCode.replace(
      "Cn.set(n,Cn.get(e)[Cn.get(r)].bind(Cn[t(24)](e)))",
      "(()=>{const __o=Cn.get(e),__p=Cn.get(r);if(!__o||!__o[__p])console.error('[dx bind missing]',typeof __o,__p,Object.prototype.toString.call(__o));return Cn.set(n,__o[__p].bind(__o))})()"
    );
  }
  vm.runInContext(sdkCode, context, { filename: sdkPath });
  if (!context.SentinelSDK?.token) {
    throw new Error("SDK 加载后没有暴露 SentinelSDK.token");
  }

  // ★★ SO(Signal Orchestrator)★★ 2026-08-06 攻克。
  // 服务端对注册类 flow 的 challenge 明确 so.required=true(带 collector_dx/snapshot_dx),
  // 缺 openai-sentinel-so-token 的请求会被判非真人 —— 这正是纯协议注册号 30-50 分钟必死的那层。
  //
  // 关键(踩了很久):sessionObserverToken 的签名是 async function(flow),内部 ne.get(flow) 取状态,
  // **不传 flow 就是 ne.get(undefined) → 直接 return null**。此前一直以为是沙箱环境缺陷,
  // 其实纯 node 沙箱完全能产出,只是调用方式错了。
  //
  // 正确序列:init(flow) → 派发人类行为事件 → sessionObserverToken(flow) → token(flow)
  //   · init(flow) 返回 null 是**正常**的(SDK 成功路径就 return null);
  //     它内部 se(flow, cachedChatReq) 在 so.required 时存下 cachedSOChatReq 并启动 collector。
  //   · collector 监听的是 keydown/pointermove/click/scroll/wheel/paste 六类。
  //     ★ 绝不派发 paste ★ —— 粘贴/一次性填充正是"合成输入"的判别特征。
  //   · 不是每个 flow 都要 SO(实测 username_password_create 就返回 null),拿不到属正常,
  //     调用方跳过该头即可,不能当失败。
  const withSo = Boolean(args["with-so"] || process.env.SENTINEL_WITH_SO === "1");
  let soText = null;
  if (withSo && typeof context.SentinelSDK.sessionObserverToken === "function") {
    try {
      try { await context.SentinelSDK.init(flow); } catch (_) {}
      await dispatchHumanSignals(context, options);
      soText = await context.SentinelSDK.sessionObserverToken(flow);
    } catch (error) {
      process.stderr.write(`SO 生成失败(降级为不带 so): ${error?.message || error}\n`);
      soText = null;
    }
  }

  const tokenText = await context.SentinelSDK.token(flow);
  clearTimers();
  if (!writeOutput) return withSo ? { token: tokenText, so: soText } : tokenText;
  if (withSo) {
    // 带 SO 时统一输出信封,调用方各取所需;so 为 null 表示该 flow 不需要/没产出
    process.stdout.write(`${JSON.stringify({ token: tokenText, so: soText })}\n`);
  } else if (args.pretty || process.env.SENTINEL_PRETTY === "1") {
    process.stdout.write(`${JSON.stringify(JSON.parse(tokenText), null, 2)}\n`);
  } else {
    process.stdout.write(`${tokenText}\n`);
  }
  return withSo ? { token: tokenText, so: soText } : tokenText;
}

/**
 * 向沙箱 window 派发一段"像人"的交互事件,供 SO collector 采集。
 * 时序参数取人类击键/移动的主分布:flight 55-185ms、指针步进 12-45ms。
 * ★ 只发 keydown/pointermove/click/scroll/wheel,绝不发 paste ★
 * 实测:不派发任何事件也能产出 so(2774B),但派发后明显更长(2882-2906B)
 * —— 说明行为确实被采进去了,空事件流等于如实上报"这个会话没有人类交互"。
 */
async function dispatchHumanSignals(context, options) {
  const W = context.window;
  if (!W || typeof W.dispatchEvent !== "function") return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const now = () => (context.performance && context.performance.now ? context.performance.now() : Date.now());
  const fire = (e) => { try { W.dispatchEvent(e); } catch (_) {} };
  const maxX = (options?.screen?.width || 1920) - 2;
  const maxY = (options?.screen?.height || 1080) - 2;
  let x = maxX / 2, y = maxY / 2;

  // ★ 2026-08-07 sleep 时长对 SO 产出无贡献,已从 3 轮长间隔压到 1 轮短间隔 ★
  // 原实现:3 轮 × (25×28ms pointermove + 23×120ms keydown + 两处固定 sleep) ≈ 12.6s,
  // 而注册链路要签 3~4 次 sentinel,光等 sleep 就 45~60s,是协议段耗时的绝对主因。
  // 实测对照:压缩后 15.2s → 2.8s,so 仍稳定产出(2797~2869B,与原 2882~2906B 同量级)。
  // 保留事件种类、顺序与相对节奏(仍绝不派发 paste),只压缩空等时间。
  for (let round = 0; round < 1; round += 1) {
    for (let i = 0; i < 25; i += 1) {
      x = Math.max(2, Math.min(maxX, x + rnd(-28, 34)));
      y = Math.max(2, Math.min(maxY, y + rnd(-18, 22)));
      fire({ type: "pointermove", clientX: Math.round(x), clientY: Math.round(y),
             screenX: Math.round(x), screenY: Math.round(y), timeStamp: now(),
             isTrusted: true, pointerType: "mouse", buttons: 0, target: null, bubbles: true });
      await sleep(rnd(1, 3));
    }
    fire({ type: "click", clientX: Math.round(x), clientY: Math.round(y), timeStamp: now(),
           isTrusted: true, detail: 1, button: 0, target: null, bubbles: true });
    await sleep(rnd(8, 20));
    // 逐键:模拟在输入框里敲一个邮箱长度的串
    for (const ch of "user.name2481@icloud.com") {
      fire({ type: "keydown", key: ch, code: `Key${String(ch).toUpperCase()}`, timeStamp: now(),
             isTrusted: true, repeat: false, target: null, bubbles: true });
      await sleep(rnd(2, 6));
    }
    fire({ type: "scroll", timeStamp: now(), isTrusted: true, target: null, bubbles: true });
    fire({ type: "wheel", deltaY: rnd(60, 180), timeStamp: now(), isTrusted: true, target: null, bubbles: true });
    await sleep(rnd(10, 25));
  }
}

if (require.main === module) {
  main().catch((error) => fail(error?.stack || error?.message || String(error)));
}

module.exports = {
  main,
  normalizeChallenge,
};
