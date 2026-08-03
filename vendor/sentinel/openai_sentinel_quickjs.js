const EXPOSE_PATCH = "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t}({});";
const EXPOSE_REPLACEMENT =
  "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t.__debug_n=_n,t.__debug_bindProof=D,t}({});";
const INSTANCE_PATCH = "var P=new _;";
const INSTANCE_REPLACEMENT = "var P=new _;globalThis.__debugP=P;";
const SDK_GLOBAL_PATCH = "var SentinelSDK=";
const SDK_GLOBAL_REPLACEMENT = "globalThis.SentinelSDK=";

function bytesToBase64(bytes) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let out = "";
  let i = 0;
  while (i < bytes.length) {
    const b0 = bytes[i++] || 0;
    const b1 = bytes[i++] || 0;
    const b2 = bytes[i++] || 0;
    const n = (b0 << 16) | (b1 << 8) | b2;
    out += chars[(n >> 18) & 63];
    out += chars[(n >> 12) & 63];
    out += i - 2 < bytes.length ? chars[(n >> 6) & 63] : "=";
    out += i - 1 < bytes.length ? chars[n & 63] : "=";
  }
  return out;
}

function base64ToBytes(base64) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const clean = String(base64 || "").replace(/[^A-Za-z0-9+/=]/g, "");
  const bytes = [];
  for (let i = 0; i < clean.length; i += 4) {
    const c0 = chars.indexOf(clean[i]);
    const c1 = chars.indexOf(clean[i + 1]);
    const c2 = chars.indexOf(clean[i + 2]);
    const c3 = chars.indexOf(clean[i + 3]);
    const n = ((c0 & 63) << 18) | ((c1 & 63) << 12) | (((c2 < 0 ? 0 : c2) & 63) << 6) | ((c3 < 0 ? 0 : c3) & 63);
    bytes.push((n >> 16) & 255);
    if (clean[i + 2] !== "=") bytes.push((n >> 8) & 255);
    if (clean[i + 3] !== "=") bytes.push(n & 255);
  }
  return bytes;
}

function createStorage() {
  const map = new Map();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key) {
      return map.has(String(key)) ? map.get(String(key)) : null;
    },
    setItem(key, value) {
      map.set(String(key), String(value));
    },
    removeItem(key) {
      map.delete(String(key));
    },
  };
}

function createElement(tagName) {
  const tag = String(tagName || "div").toLowerCase();
  return {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    nodeName: tag.toUpperCase(),
    style: {},
    children: [],
    src: "",
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((x) => x !== child);
      return child;
    },
    setAttribute() {},
    getAttribute() {
      return null;
    },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() {
      return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 };
    },
  };
}

function makePluginArray() {
  const names = [
    "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
    "Microsoft Edge PDF Viewer", "WebKit built-in PDF",
  ];
  const arr = [];
  names.forEach((name, i) => {
    arr[i] = {
      name,
      filename: name.toLowerCase().replace(/ /g, "-") + ".pdf",
      description: name + " for PDF files",
      suffixes: "pdf",
    };
  });
  arr.length = names.length;
  arr.item = (i) => arr[i] || null;
  arr.namedItem = (n) => arr.find((p) => p && p.name === n) || null;
  arr.refresh = () => {};
  return arr;
}

function makeMimeTypes() {
  const names = ["application/pdf", "text/pdf"];
  const arr = [];
  names.forEach((name, i) => {
    arr[i] = { type: name, suffixes: "pdf", description: "Portable Document Format", enabledPlugin: null };
  });
  arr.length = names.length;
  arr.item = (i) => arr[i] || null;
  arr.namedItem = (n) => arr.find((m) => m && m.type === n) || null;
  arr.refresh = () => {};
  return arr;
}

// 真 Chrome navigator 的对象型属性：Symbol.toStringTag 让 Object.prototype.toString 产出真实类名
// （如 [object NavigatorLogin]），SDK 的 T() 会做 navigator[key].toString() 采样（p[10]）。
function taggedChrome(proto) {
  const obj = {};
  if (proto && proto !== "object") Object.defineProperty(obj, Symbol.toStringTag, { value: String(proto) });
  return obj;
}

// Navigator.prototype 的候选键（真 Chrome 大部分导航属性都在原型上；SDK 采样 Object.keys(proto)）。
// 值必须 defined，否则 navigator[key].toString() 抛错 → catch → 返回裸 key。
function chromeNavigatorExtras() {
  return {
    userAgentData: taggedChrome("NavigatorUAData"),
    login: taggedChrome("NavigatorLogin"),
    keyboard: taggedChrome("Keyboard"),
    getInterestGroupAdAuctionData: function getInterestGroupAdAuctionData() {},
    storage: taggedChrome("StorageManager"),
    credentials: taggedChrome("CredentialsContainer"),
    permissions: taggedChrome("Permissions"),
    connection: Object.assign(taggedChrome("NetworkInformation"), { effectiveType: "4g", rtt: 50, downlink: 10, saveData: false }),
    serviceWorker: taggedChrome("ServiceWorkerContainer"),
    mediaDevices: taggedChrome("MediaDevices"),
    geolocation: taggedChrome("Geolocation"),
    bluetooth: taggedChrome("Bluetooth"),
    usb: taggedChrome("USB"),
    hid: taggedChrome("HID"),
    serial: taggedChrome("Serial"),
    gpu: taggedChrome("GPU"),
    ink: taggedChrome("Ink"),
    wakeLock: taggedChrome("WakeLock"),
    xr: taggedChrome("XRSystem"),
    mediaCapabilities: taggedChrome("MediaCapabilities"),
    scheduling: taggedChrome("Scheduling"),
    mediaSession: taggedChrome("MediaSession"),
    clipboard: taggedChrome("Clipboard"),
    devicePosture: taggedChrome("DevicePosture"),
    virtualKeyboard: taggedChrome("VirtualKeyboard"),
    windowControlsOverlay: taggedChrome("WindowControlsOverlay"),
    managed: taggedChrome("NavigatorManagedData"),
    getGamepads: function getGamepads() { return []; },
    getBattery: async function getBattery() { return { charging: false, level: 1, chargingTime: 0, dischargingTime: Infinity }; },
    requestMIDIAccess: async function requestMIDIAccess() { return {}; },
    pdfViewerEnabled: true,
  };
}

function installRuntime(payload) {
  const __realST = setTimeout.bind(null);
  const __realCST = clearTimeout.bind(null);
  // 我们注入的全局一律 non-enumerable：SDK 疑似对 window 键做随机采样，
  // 枚举可见的 __sentinel_*/__debug*/__vm_* 会泄漏 vm 执行痕迹进指纹（已实证捕获）。
  function defineHidden(key, value) {
    try {
      Object.defineProperty(globalThis, key, {
        value, writable: true, configurable: true, enumerable: false,
      });
    } catch (e) {
      globalThis[key] = value;
    }
  }
  const screen = {
    width: Number(payload.screen_width || 1366),
    height: Number(payload.screen_height || 768),
    availWidth: Number(payload.screen_width || 1366),
    availHeight: Number(payload.screen_height || 768),
    availLeft: 0,
    availTop: 0,
    colorDepth: 24,
    pixelDepth: 24,
  };
  // 复刻真页面 DOM：head 里 script 为 backend-api 加载器。
  // 真浏览器 p[5] 稳定 = 加载器 src（backend-api URL）；SDK 读 script 元素的 index 会变化，
  // 故只放一个元素，任意 index 都返回 backend-api。
  const scripts = [];
  const _loaderScriptEl = createElement("script");
  _loaderScriptEl.src = String(payload.script_src || "https://sentinel.openai.com/backend-api/sentinel/sdk.js");
  scripts.push(_loaderScriptEl);
  const documentElement = createElement("html");
  documentElement.clientWidth = screen.width;
  documentElement.clientHeight = screen.height;
  const document = {
    readyState: "complete",
    hidden: false,
    visibilityState: "visible",
    referrer: "https://auth.openai.com/",
    URL: "https://auth.openai.com/",
    location: {
      href: "https://auth.openai.com/about-you",
      origin: "https://auth.openai.com",
      protocol: "https:",
      host: "auth.openai.com",
      hostname: "auth.openai.com",
      pathname: "/about-you",
      search: "",
      hash: "",
    },
    cookie: `oai-did=${encodeURIComponent(payload.device_id || "")}`,
    scripts,
    currentScript: _loaderScriptEl,  // 真浏览器 p[5]=backend-api 加载器 src（SDK 读 currentScript.src）
    documentElement,
    body: createElement("body"),
    head: createElement("head"),
    createElement(tag) {
      const el = createElement(tag);
      if (String(tag).toLowerCase() === "script") scripts.push(el);
      return el;
    },
    createElementNS(_ns, tag) {
      return this.createElement(tag);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    getElementById() {
      return null;
    },
    getElementsByTagName() {
      return [];
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return true;
    },
  };

  // time_origin 由调用方按「一次注册」固定传入（A3：真浏览器是页面加载常数，同注册两 token 必须一致）。
  // performance.now() = Date.now() - timeOrigin：真语义，从 ~0 起单调递增。
  const _timeOrigin = Number(payload.time_origin || (Date.now() - 300));
  // perf_scale>1 时加速性能时钟：turnstile 程序有 ABSCOND(|now-prev|>2000ms)→加载 blob1 慢路径，
  // vm 执行太快永不触发。加速时钟用于验证该分支是否影响 t 长度。
  const _perfScale = Number(payload.perf_scale || 1) || 1;
  const performance = {
    now: () => {
      const v = Date.now() - _timeOrigin;
      return v > 0 ? v * _perfScale : 0;
    },
    timeOrigin: _timeOrigin,
    memory: { jsHeapSizeLimit: Number(payload.js_heap_size_limit || 4395630592) },
  };

  class TextEncoderPoly {
    encode(text) {
      const str = String(text || "");
      const out = new Uint8Array(str.length);
      for (let i = 0; i < str.length; i += 1) out[i] = str.charCodeAt(i) & 255;
      return out;
    }
  }

  class TextDecoderPoly {
    decode(input) {
      if (!input) return "";
      let out = "";
      for (let i = 0; i < input.length; i += 1) {
        out += String.fromCharCode(input[i]);
      }
      return out;
    }
  }

  class URLSearchParamsPoly {
    constructor(search) {
      this._pairs = [];
      const s = String(search || "").replace(/^\?/, "");
      if (!s) return;
      const parts = s.split("&");
      for (const p of parts) {
        if (!p) continue;
        const i = p.indexOf("=");
        if (i < 0) {
          this._pairs.push([decodeURIComponent(p), ""]);
        } else {
          this._pairs.push([
            decodeURIComponent(p.slice(0, i)),
            decodeURIComponent(p.slice(i + 1)),
          ]);
        }
      }
    }
    keys() {
      return this._pairs.map((x) => x[0])[Symbol.iterator]();
    }
  }

  class URLPoly {
    constructor(input, base) {
      const raw = String(input || "");
      if (/^https?:\/\//i.test(raw)) {
        this.href = raw;
      } else {
        const b = String(base || "https://auth.openai.com/").replace(/\/$/, "");
        this.href = `${b}/${raw.replace(/^\//, "")}`;
      }
      const m = this.href.match(/^(https?:)\/\/([^\/]+)(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i);
      this.protocol = m ? m[1] : "https:";
      this.host = m ? m[2] : "auth.openai.com";
      this.hostname = this.host;
      this.pathname = m && m[3] ? m[3] : "/";
      this.search = m && m[4] ? m[4] : "";
      this.hash = m && m[5] ? m[5] : "";
      this.origin = `${this.protocol}//${this.host}`;
    }
    toString() {
      return this.href;
    }
  }

  globalThis.window = globalThis;
  globalThis.self = globalThis;
  globalThis.top = globalThis;
  globalThis.parent = globalThis;
  // window 尺寸/DPR：真浏览器必有，缺失会让 SDK 退回奇怪默认值（实测 p[0]=3000）
  globalThis.innerWidth = screen.width;
  globalThis.innerHeight = screen.height;
  globalThis.outerWidth = screen.width;
  globalThis.outerHeight = screen.height;
  globalThis.devicePixelRatio = 1;
  globalThis.document = document;
  // 真 Chrome：navigator 属性大多在 Navigator.prototype 上，SDK 的 T() 采样
  // Object.keys(Object.getPrototypeOf(navigator)) 并做 navigator[key].toString()（p[10]）。
  // 所以除自身属性外，必须把候选键放到 navigator 的 __proto__ 上。
  const navigatorProps = {
    userAgent: String(payload.user_agent || "Mozilla/5.0"),
    language: String(payload.language || "en-US"),
    languages: Array.isArray(payload.languages) ? payload.languages : ["en-US", "en"],
    hardwareConcurrency: Number(payload.hardware_concurrency || 16),
    platform: "Win32",
    vendor: "Google Inc.",
    vendorSub: "",  // 真 Chrome = ''（typeof string）；缺失会让 SDK 报 undefined（A1）
    webdriver: false,
    deviceMemory: payload.device_memory != null ? Number(payload.device_memory) : 8,
    maxTouchPoints: payload.max_touch_points != null ? Number(payload.max_touch_points) : 0,
    cookieEnabled: true,
    onLine: true,
    plugins: makePluginArray(),   // 真 Chrome PluginArray（typeof object，A1）
    mimeTypes: makeMimeTypes(),   // 真 Chrome MimeTypeArray（typeof object，A1）
  };
  const navigatorProto = { ...navigatorProps, ...chromeNavigatorExtras() };
  Object.setPrototypeOf(navigatorProps, navigatorProto);
  Object.defineProperty(globalThis, 'navigator', { value: navigatorProps, configurable: true, writable: true });
  globalThis.location = {
    href: "https://auth.openai.com/",
    origin: "https://auth.openai.com",
    pathname: "/",
    search: "",
  };
  globalThis.screen = screen;
  globalThis.performance = performance;
  // 真浏览器 auth.openai.com 的 localStorage 有 Statsig 键（turnstile 指纹计算 Object.keys(localStorage) 读取）
  const _ls = createStorage();
  try {
    const _sid = String(payload.statsig_id || "444584300");
    _ls.setItem("statsig.stable_id." + _sid, JSON.stringify(String(payload.statsig_stable_id || "")));
    _ls.setItem("statsig.session_id." + _sid, JSON.stringify({
      sessionID: String(payload.statsig_session_id || ""),
      startTime: Number(_timeOrigin),
      lastSeen: Number(_timeOrigin),
      isLoggedIn: false,
    }));
  } catch (e) { /* 尽力 */ }
  globalThis.localStorage = _ls;
  globalThis.sessionStorage = createStorage();
  // __sentinel_*/SentinelSDK 是真浏览器全局（backend-api 加载器创建，可枚举），保持忠实
  globalThis.__sentinel_init_pending = [];
  globalThis.__sentinel_token_pending = [];
  globalThis.SentinelSDK = globalThis.SentinelSDK || {};
  // 仅我们的 vm/诊断产物需要隐藏（真浏览器没有 __debug_*/__vm_*）
  for (const _k of [
    "__payload_json", "__sdk_source", "__vm_done", "__vm_output_json", "__vm_error",
    "__debugP", "__debug_D", "__debug_se",
  ]) {
    defineHidden(_k, globalThis[_k]);
  }
  // Node 专属全局（process/Buffer/global）真浏览器没有，window 键采样会命中 → 只隐藏枚举（保留值，
  // 否则 wrapper 的 process.stdout/stdin 会挂）。process 非可配置时静默失败（尽力而为）。
  for (const _n of ["process", "Buffer", "global"]) {
    try {
      Object.defineProperty(globalThis, _n, {
        value: globalThis[_n], writable: true, configurable: true, enumerable: false,
      });
    } catch (e) { /* 非可配置则忽略 */ }
  }

  globalThis.setTimeout = (cb, ms) => {
    const d = typeof ms === "number" ? ms : 0;
    if (d >= 100) return __realST(cb, d);   // 大延迟（看门狗/超时）→ 真异步
    cb(); return 1;                          // 小延迟/0 → 同步，解释器快跑
  };
  globalThis.clearTimeout = (id) => __realCST(id);
  globalThis.setInterval = () => 1;
  globalThis.clearInterval = () => {};
  globalThis.requestIdleCallback = (cb) => {
    if (typeof cb === "function") cb({ didTimeout: false, timeRemaining: () => 50 });
    return 1;
  };
  globalThis.cancelIdleCallback = () => {};
  // 真实事件系统：session observer 通过 addEventListener 采行为数据。
  // 记录注册的事件类型（供诊断），并让 dispatchEvent 能触发回调（模拟行为喂给 collector）。
  const _listeners = {};
  const _eventLog = [];
  defineHidden("__event_log", _eventLog);
  function _regEvent(type, cb) {
    const t = String(type || "");
    if (!t) return;
    if (typeof cb === "function") {
      (_listeners[t] = _listeners[t] || []).push(cb);
      _eventLog.push(`register:${t}`);
    }
  }
  function _fire(type, ev) {
    for (const cb of _listeners[type] || []) {
      try { cb(ev || { type, target: globalThis, timeStamp: performance.now() }); } catch (e) { /* ignore */ }
    }
  }
  globalThis.addEventListener = _regEvent;
  globalThis.removeEventListener = (t, cb) => {
    const arr = _listeners[String(t)];
    if (arr) _listeners[String(t)] = arr.filter((x) => x !== cb);
  };
  globalThis.dispatchEvent = (ev) => { _fire(ev && ev.type, ev); return true; };
  globalThis.postMessage = () => {};
  defineHidden("__fire_event", _fire);  // 供 solve 模拟行为时派发事件
  // document 也走同一事件系统（observer 可能在 document 上注册）
  document.addEventListener = _regEvent;
  document.removeEventListener = (t, cb) => {
    const arr = _listeners[String(t)];
    if (arr) _listeners[String(t)] = arr.filter((x) => x !== cb);
  };
  document.dispatchEvent = (ev) => { _fire(ev && ev.type, ev); return true; };

  globalThis.atob = (input) => String.fromCharCode(...base64ToBytes(input));
  globalThis.btoa = (input) => {
    const str = String(input || "");
    const bytes = [];
    for (let i = 0; i < str.length; i += 1) bytes.push(str.charCodeAt(i) & 255);
    return bytesToBase64(bytes);
  };
  globalThis.TextEncoder = globalThis.TextEncoder || TextEncoderPoly;
  globalThis.TextDecoder = globalThis.TextDecoder || TextDecoderPoly;
  globalThis.URL = globalThis.URL || URLPoly;
  globalThis.URLSearchParams = globalThis.URLSearchParams || URLSearchParamsPoly;
  globalThis.Event =
    globalThis.Event ||
    class Event {
      constructor(type) {
        this.type = type;
      }
    };
  globalThis.CustomEvent =
    globalThis.CustomEvent ||
    class CustomEvent extends globalThis.Event {
      constructor(type, init) {
        super(type);
        this.detail = init && Object.prototype.hasOwnProperty.call(init, "detail") ? init.detail : null;
      }
    };
  globalThis.MessageChannel =
    globalThis.MessageChannel ||
    class MessageChannel {
      constructor() {
        this.port1 = { postMessage() {}, addEventListener() {}, removeEventListener() {}, start() {}, close() {} };
        this.port2 = { postMessage() {}, addEventListener() {}, removeEventListener() {}, start() {}, close() {} };
      }
    };
  globalThis.matchMedia =
    globalThis.matchMedia ||
    ((query) => ({
      media: String(query || ""),
      matches: false,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    }));
  globalThis.getComputedStyle =
    globalThis.getComputedStyle ||
    (() => ({
      getPropertyValue() {
        return "";
      },
    }));
  globalThis.history = globalThis.history || { length: 1, state: null, back() {}, forward() {}, go() {}, pushState() {}, replaceState() {} };
  globalThis.chrome = globalThis.chrome || { runtime: {}, app: {} };
  globalThis.CSS = globalThis.CSS || { supports() { return true; } };
  globalThis.indexedDB =
    globalThis.indexedDB ||
    {
      open() {
        return { onerror: null, onsuccess: null, onupgradeneeded: null, result: {}, error: null };
      },
      deleteDatabase() {
        return {};
      },
    };
  globalThis.fetch = async () => {
    throw new Error("fetch should not be called");
  };

  const randomFill = (arr) => {
    for (let i = 0; i < arr.length; i += 1) {
      arr[i] = Math.floor(Math.random() * 256);
    }
    return arr;
  };
  const _origCrypto = globalThis.crypto;
  const _origRandomUUID =
    _origCrypto && typeof _origCrypto.randomUUID === "function"
      ? _origCrypto.randomUUID.bind(_origCrypto)
      : null;
  // 记录 randomUUID 返回值，供探针判断 p[14] 是否来自它（保真 [14] 会话内恒定）。
  const _uuidCalls = [];
  defineHidden("__debug_randomUUID_calls", _uuidCalls);
  globalThis.crypto = {
    randomUUID: _origRandomUUID
      ? () => {
          const u = _origRandomUUID();
          if (_uuidCalls) _uuidCalls.push(u);
          return u;
        }
      : () => {
          const u = "10000000-0000-4000-8000-" + String(Math.floor(Math.random() * 1e12)).padStart(12, "0");
          if (_uuidCalls) _uuidCalls.push(u);
          return u;
        },
    getRandomValues: randomFill,
  };

  // 诊断钩子（payload.debug_undef）：记录 SDK 对 navigator/document 的读取，
  // 定位 p[10]（navigator 属性采样）与 p[5]（sdk 路径）的真实来源。
  if (payload.debug_undef) {
    const reads = [];
    defineHidden("__debug_fp_reads", reads);
    const navProxy = new Proxy(navigator, {
      has(t, k) {
        if (typeof k !== "symbol") reads.push({ k: `navigator.in.${String(k)}`, v: "CALL" });
        return Reflect.has(t, k);
      },
      ownKeys(t) {
        reads.push({ k: "navigator.ownKeys", v: "CALL" });
        return Reflect.ownKeys(t);
      },
      getOwnPropertyDescriptor(t, k) {
        if (typeof k !== "symbol") reads.push({ k: `navigator.gOPD.${String(k)}`, v: "CALL" });
        return Reflect.getOwnPropertyDescriptor(t, k);
      },
      get(t, k) {
        if (typeof k !== "symbol") {
          const v = t[k];
          reads.push({ k: `navigator.${String(k)}`, v: v === undefined ? "UNDEF" : typeof v });
        }
        return t[k];
      },
    });
    const wrapRet = (label, ret) => {
      if (ret && (typeof ret === "object" || typeof ret === "function")) {
        return new Proxy(ret, {
          get(t2, k2) {
            if (typeof k2 !== "symbol") {
              const v2 = t2[k2];
              if (v2 === undefined) reads.push({ k: `${label}.${String(k2)}`, v: "UNDEF" });
              return v2;
            }
            return t2[k2];
          },
        });
      }
      return ret;
    };
    const docProxy = new Proxy(document, {
      get(t, k) {
        if (typeof k !== "symbol") {
          const v = t[k];
          reads.push({ k: `document.${String(k)}`, v: v === undefined ? "UNDEF" : (typeof v === "string" ? v.slice(0, 80) : typeof v) });
          if (typeof v === "function") {
            return (...a) => {
              reads.push({ k: `document.${String(k)}(${a.map((x) => String(x)).join(", ").slice(0, 80)})`, v: "CALL" });
              return wrapRet(`document.${String(k)}()`, t[k].apply(t, a));
            };
          }
        }
        return t[k];
      },
    });
    Object.defineProperty(globalThis, "navigator", { value: navProxy, configurable: true, writable: true });
    globalThis.document = docProxy;
    // screen / performance / crypto 包装：`_n`（turnstile 求解器）依赖这些
    for (const [gn, gk] of [["screen", "screen"], ["performance", "performance"], ["crypto", "crypto"]]) {
      const gv = globalThis[gk];
      if (gv && typeof gv === "object") {
        globalThis[gk] = new Proxy(gv, {
          get(t, k) {
            if (typeof k !== "symbol") {
              const v = t[k];
              if (v === undefined) reads.push({ k: `${gn}.${String(k)}`, v: "UNDEF" });
              return v;
            }
            return t[k];
          },
        });
      }
    }
  }
}

function loadPatchedSdk(sdkSource) {
  let sdk = String(sdkSource || "");
  sdk = sdk.replace(SDK_GLOBAL_PATCH, SDK_GLOBAL_REPLACEMENT);
  sdk = sdk.replace(INSTANCE_PATCH, INSTANCE_REPLACEMENT);
  sdk = sdk.replace(EXPOSE_PATCH, EXPOSE_REPLACEMENT);
  sdk = sdk.replace('e(""+kn)}),500', 'e(""+kn)}),120000');
  sdk = sdk.replace("function D(t,n){I.set(t,n)}", "function D(t,n){I.set(t,n);globalThis.__debug_D=D;}");
  sdk = sdk.replace("function se(t,n){const e=Hn,r=re(t);", "globalThis.__debug_se=se;function se(t,n){const e=Hn,r=re(t);");
  eval(sdk);
}

// 模拟真实用户行为，喂 session observer（pointermove/keydown/scroll/wheel/click）。
// 纯同步 + 显式递增 timeStamp（避免真实 await 拖慢/干扰 _n 的异步），坐标连续（人类鼠标轨迹）。
// 事件系统注册回调即记录，sessionObserverToken 用记录的 timeStamp/坐标。
async function simulateBehavior() {
  const fire = globalThis.__fire_event;
  if (typeof fire !== "function") return;
  let t = 0;  // 相对页面加载的毫秒
  const step = () => { t += 120 + Math.floor(Math.random() * 60); return t; };
  const pts = [
    [280, 180], [310, 205], [340, 230], [375, 255], [400, 280],
    [435, 310], [465, 340], [500, 365], [525, 390], [510, 400],
    [485, 415], [455, 430], [430, 445], [415, 455], [405, 465],
  ];
  for (const [x, y] of pts) {
    fire("pointermove", { type: "pointermove", clientX: x, clientY: y, screenX: x, screenY: y, pointerType: "mouse", buttons: 0, timeStamp: step() });
  }
  fire("wheel", { type: "wheel", deltaY: 260, clientX: 400, clientY: 300, deltaMode: 0, timeStamp: step() });
  fire("scroll", { type: "scroll", scrollY: 260, timeStamp: step() });
  fire("keydown", { type: "keydown", key: "Tab", code: "Tab", keyCode: 9, which: 9, timeStamp: step() });
  fire("click", { type: "click", clientX: 505, clientY: 400, button: 0, timeStamp: step() });
  fire("paste", { type: "paste", timeStamp: step() });
  fire("message", { type: "message", timeStamp: step() });
}

async function run(payload, sdkSource) {
  installRuntime(payload);
  loadPatchedSdk(sdkSource);

  if (payload.action === "requirements") {
    const requestP = await globalThis.__debugP.getRequirementsToken();
    return {
      request_p: requestP,
      uuid_calls: Array.isArray(globalThis.__debug_randomUUID_calls)
        ? globalThis.__debug_randomUUID_calls
        : [],
      fp_reads: Array.isArray(globalThis.__debug_fp_reads)
        ? globalThis.__debug_fp_reads
        : [],
    };
  }

  if (payload.action === "solve") {
    const challenge = payload.challenge || {};
    const requestP = String(payload.request_p || "").trim();
    if (!requestP) throw new Error("missing request_p");
    const finalP = await globalThis.__debugP.getEnforcementToken(challenge);
    globalThis.SentinelSDK.__debug_bindProof(challenge, requestP);
    const dx = challenge && challenge.turnstile ? challenge.turnstile.dx : null;
    if (typeof globalThis.__debug_D === "function") globalThis.__debug_D(challenge, requestP);
    const tValue = dx ? await globalThis.SentinelSDK.__debug_n(challenge, dx) : null;
    // [so] se + sessionObserverToken 产真 so（getEnforcementToken 已设寄存器）
    let so = null;
    try {
      const _flow = String(payload.flow || "");
      if (typeof globalThis.__debug_se === "function") globalThis.__debug_se(_flow, challenge);
      // 注意: 不要在这里等待/延迟——collector 的 jt 与 snapshot 的 jt 共享全局 St/Y，会互相干扰挂起。
      // 模拟真实用户行为，喂 session observer（实验性；监听器异步注册时序未解决）
      await simulateBehavior();
      if (typeof globalThis.SentinelSDK.sessionObserverToken === "function") {
        const s = await globalThis.SentinelSDK.sessionObserverToken(_flow);
        so = (typeof s === "string") ? s : JSON.stringify(s);
      }
    } catch (e) { /* so 失败不影响 t */ }
    // 调试：dump session observer 采集的 __oai_so_* 状态
    const oai_so = {};
    try {
      for (const k of Object.keys(globalThis)) {
        if (k.startsWith("__oai_so_")) oai_so[k] = String(globalThis[k]).slice(0, 60);
      }
    } catch (e) { /* ignore */ }
    return {
      final_p: finalP,
      t: tValue,
      so: so,
      fp_reads: Array.isArray(globalThis.__debug_fp_reads) ? globalThis.__debug_fp_reads : [],
      event_log: Array.isArray(globalThis.__event_log) ? globalThis.__event_log : [],
      oai_so: oai_so,
    };
  }

  throw new Error(`unsupported action: ${payload.action}`);
}

(async () => {
  try {
    const payload = JSON.parse(String(globalThis.__payload_json || "{}"));
    const sdkSource = String(globalThis.__sdk_source || "");
    const result = await run(payload, sdkSource);
    globalThis.__vm_output_json = JSON.stringify(result);
  } catch (error) {
    const detail = {
      name: error && error.name ? String(error.name) : "Error",
      message: error && error.message ? String(error.message) : String(error),
      stack: error && error.stack ? String(error.stack) : String(error),
    };
    const message = `${detail.name}: ${detail.message}\n${detail.stack}`;
    globalThis.__vm_error = message;
  } finally {
    globalThis.__vm_done = true;
  }
})();
