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
      const g = globalThis.__font_gbcr;
      if (g && typeof g === "object") {
        return {
          x: Number(g.x || 0), y: Number(g.y || 0), width: Number(g.width || 0),
          height: Number(g.height || 0), top: Number(g.top || 0), left: Number(g.left || 0),
          right: Number(g.right || 0), bottom: Number(g.bottom || 0),
        };
      }
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
  // 附加 scripts（实验：模拟浏览器真实 document.scripts，SCRIPTS opcode 读 src 匹配）
  if (Array.isArray(payload.extra_scripts)) {
    for (const _src of payload.extra_scripts) {
      const _el = createElement("script");
      _el.src = String(_src);
      scripts.push(_el);
    }
  }
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
  // perf_jump=true：performance.now 每次调用比上次 +2500ms（强制触发 ABSCOND 慢路径），
  // 验证时序分支是否贡献 t 长度。首次调用返回真实值。
  let _jumpInit = false;
  let _jumpLast = 0;
  const performance = {
    now: () => {
      const v = Date.now() - _timeOrigin;
      const base = v > 0 ? v * _perfScale : 0;
      if (payload.perf_jump) {
        if (!_jumpInit) { _jumpInit = true; _jumpLast = base; return base; }
        _jumpLast += 2500;
        return _jumpLast;
      }
      return base;
    },
    timeOrigin: _timeOrigin,
    memory: { jsHeapSizeLimit: Number(payload.js_heap_size_limit || 4395630592) },
  };
  // 运行时 trace：始终记录 performance.now / Math.random 调用序列（对比学习），
  // 有 payload.runtime_trace 时按 browser 序列复刻（验证 t 是否确定性可复刻）。
  const _trNow = [], _trRand = [];
  const _basePN = performance.now.bind(performance);
  const _baseMR = globalThis.Math.random;
  const _nt = (payload.runtime_trace && Array.isArray(payload.runtime_trace.now)) ? payload.runtime_trace.now : [];
  const _rt = (payload.runtime_trace && Array.isArray(payload.runtime_trace.rand)) ? payload.runtime_trace.rand : [];
  let _ni = 0, _ri = 0;
  performance.now = () => {
    const v = _ni < _nt.length ? _nt[_ni++] : _basePN();
    _trNow.push(v);
    return v;
  };
  globalThis.Math.random = () => {
    const v = _ri < _rt.length ? _rt[_ri++] : _baseMR();
    _trRand.push(v);
    return v;
  };
  defineHidden("__trace_now", _trNow);
  defineHidden("__trace_rand", _trRand);
  // 捕获 unhandled rejection（假 so 根因：_t() 执行 TypeError 可能是 promise rejection）
  if (typeof process !== "undefined" && process.on) {
    try {
      process.on("unhandledRejection", (reason) => {
        globalThis.__so_rej = (reason && reason.stack) || String(reason);
      });
    } catch (e) { /* ignore */ }
  }
  // 包装 Reflect.set 捕获 "Assignment to constant variable"（vm 特有全局不可写 → TypeError 根因定位）
  if (payload.debug_reflect) {
    const _origRS = Reflect.set.bind(Reflect);
    Reflect.set = function (target, prop, value) {
      try {
        return _origRS(target, prop, value);
      } catch (e) {
        globalThis.__reflect_err = {
          prop: String(prop), targetType: target === null ? "null" : typeof target,
          targetStr: (target && typeof target === "object") ? String(Object.getPrototypeOf(target)) : "",
          msg: String(e && e.message || e),
        };
        throw e;
      }
    };
  }

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
    platform: "MacIntel",
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
  // 附加自定义 localStorage 键（实验：补全真浏览器实际键，缩小 Object.keys 缺口）
  if (payload.ls_extra && typeof payload.ls_extra === "object") {
    for (const _k of Object.keys(payload.ls_extra)) {
      try { _ls.setItem(_k, String(payload.ls_extra[_k])); } catch (e) { /* ignore */ }
    }
  }
  // __reactRouterContext 注入（实验：blob2 读 state.loaderData.root.clientBootstrap.cf*）
  if (payload.react_router_full && typeof payload.react_router_full === "object") {
    try { globalThis.__reactRouterContext = payload.react_router_full; } catch (e) { /* ignore */ }
  } else if (payload.react_router) {
    try { globalThis.__reactRouterContext = { state: { loaderData: {} } }; } catch (e) { /* ignore */ }
  }
  globalThis.sessionStorage = createStorage();
  // 字体渲染测量真值（实验：createElement().getBoundingClientRect 返回真浏览器值）
  if (payload.font_gbcr && typeof payload.font_gbcr === "object") {
    globalThis.__font_gbcr = payload.font_gbcr;
  }
  // 附加 window 全局（实验：穷举注入浏览器页面全局，定位 t 缺口）
  if (payload.win_extra && typeof payload.win_extra === "object") {
    for (const _k of Object.keys(payload.win_extra)) {
      try {
        if (!(_k in globalThis)) globalThis[_k] = payload.win_extra[_k];
      } catch (e) { /* ignore */ }
    }
  }
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
  // strip_node_globals=true（实验）：把 process/Buffer 设 undefined，SDK Reflect.set 它们不抛
  // "Assignment to constant variable"（vm 特有全局不可写 → TypeError 根因假设）。
  if (payload.strip_node_globals) {
    for (const _n of ["process", "Buffer", "global"]) {
      try { globalThis[_n] = undefined; } catch (e) { /* ignore */ }
    }
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
  // 捕获 snapshot_dx(Nt) 执行错误栈：TypeError 在这里被 catch 吞掉(假 so 根因定位)
  // 注意：实时 sdk 用 Nt(e[n(1)])（混淆索引），非 e.snapshot_dx
  sdk = sdk.replace(
    'try{return await Nt(e[n(1)])}catch{return null}',
    'try{if(typeof globalThis.__snap_inject==="function")globalThis.__snap_inject();return await Nt(e[n(1)])}catch(e){globalThis.__so_n_err=(e&&e.stack)||String(e);return null}'
  );
  globalThis.__patch_n = sdk.includes("__so_n_err") ? "OK" : "MISS";
  // 捕获 SETREF handler 内部错误（TypeError: Assignment to constant variable 定位）
  sdk = sdk.replace(
    "(n,e)=>St[t(10)](n,St[t(25)](e))",
    "(n,e)=>{try{return St[t(10)](n,St[t(25)](e))}catch(er){globalThis.__setref_err={n:String(n),e:String(e),nval:String(St.get(n)).slice(0,60),eval:String(St.get(e)).slice(0,60),stack:(er&&er.stack)||String(er)};throw er}}"
  );
  // patch XOR handler 记录实际 key(St.get(e)) + 字段(St.get(n)) —— 解码 so 的关键
  sdk = sdk.replace(
    'St[t(10)](G,((n,e)=>St[t(10)](n,Rt(""+St[t(25)](n),""+St.get(e)))))',
    'St[t(10)](G,((n,e)=>{(globalThis.__xor_full=(globalThis.__xor_full||[])).push([String(n),String(St.get(e)),String(St.get(n)).slice(0,60)]);return St[t(10)](n,Rt(""+St[t(25)](n),""+St.get(e)))}))'
  );
  // dump jt 的 St(At) 操作码表映射，定位 op=8 是哪个 handler
  sdk = sdk.replace(
    "St[t(10)](ht,(()=>{}))}(),Ct=0,St.set(it,n)",
    "St[t(10)](ht,(()=>{}))}(),globalThis.__St=St,globalThis.__At_dump=[...St.entries()].map(([k,v])=>[String(k),typeof v==='function'?(v+'').slice(0,220):typeof v]),Ct=0,St.set(it,n)"
  );
  // 记录 jt 执行器 catch 的完整错误栈（假 so 根因：TypeError 在 _t() 执行时抛，这里被吞）
  sdk = sdk.replace(
    "catch(t){s(btoa(Ct+\": \"+t))}",
    "catch(t){globalThis.__so_jt_err=(t&&t.stack)||String(t);s(btoa(Ct+\": \"+t))}"
  );
  globalThis.__patch_jt = sdk.includes("__so_jt_err") ? "OK" : "MISS";
  // 诊断：_t() 队列处理器加循环守卫（collector 死循环定位用）
  sdk = sdk.replace(
    "_t(){const t=xt;for(;St[t(25)](Y).length>0;){const[n,...e]=St[t(25)](Y).shift(),r=St.get(n)(...e);r&&typeof r[t(13)]===t(17)&&await r,Ct++}}",
    "_t(){const t=xt;globalThis.__T_ITER=0;for(;St[t(25)](Y).length>0;){if(++globalThis.__T_ITER>500000){throw new Error('_t_LOOP')}const[n,...e]=St[t(25)](Y).shift();try{globalThis.__T_LAST=[n,typeof St.get(n),e.length];var __r=St.get(n)(...e);}catch(er){globalThis.__T_ERR={op:n,optype:typeof St.get(n),nargs:e.length,args:e.map(x=>{try{const v=St.get(x);return [String(x),v===undefined?'undef':(typeof v==='object'?'obj':String(v).slice(0,80))]}catch{return [String(x),'?']}}),stack:(er&&er.stack)||String(er)};throw er;}__r&&typeof __r[t(13)]===t(17)&&await __r,Ct++}}"
  );
  sdk = sdk.replace("function D(t,n){I.set(t,n)}", "function D(t,n){I.set(t,n);globalThis.__debug_D=D;}");
  sdk = sdk.replace("function se(t,n){const e=Hn,r=re(t);", "globalThis.__debug_se=se;function se(t,n){const e=Hn,r=re(t);");
  eval(sdk);
}

// 模拟真实用户行为，喂 session observer（pointermove/keydown/scroll/wheel/click）。
// 2026-08-14 改进(冲击试用资格): 随机轨迹 + 自然节奏 + 真实键盘字符,
// 让 collector 采集的字段量级逼近真实浏览器分布(i 42-56, s 3882-38725, cs 1000-1400)。
// 旧版固定 15 坐标 + 固定 130ms 太规律, 字段量级只有真人零头, 过不了资格门槛。
// ★绝不派发 paste★(合成输入判别特征); 键盘用真实字符而非 Tab。
async function simulateBehavior() {
  const fire = globalThis.__fire_event;
  if (typeof fire !== "function") return;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const rnd = (a, b) => a + Math.random() * (b - a);
  const rint = (a, b) => Math.floor(rnd(a, b));

  // 随机起点(屏幕内常见交互区), 人类鼠标轨迹(随机步长 ±25/±20, 逐渐漂移)
  let x = rnd(200, 600), y = rnd(200, 400);
  const moves = rint(28, 48);  // 28-48 次移动 → i 逼近真实 42-56
  for (let i = 0; i < moves; i++) {
    x += rnd(-25, 25); y += rnd(-20, 20);
    x = Math.max(60, Math.min(1800, x));
    y = Math.max(60, Math.min(900, y));
    fire("pointermove", { type: "pointermove", clientX: x, clientY: y, screenX: x, screenY: y, pointerType: "mouse", buttons: 0, timeStamp: performance.now() });
    await wait(rnd(18, 85));  // 人类鼠标移动间隔 18-85ms(非固定)
  }
  // 确定性滚动(register-kit 对齐: 每轮都滚, 保证 so 行为字段完整)
  {
    const dy = rint(100, 400);
    fire("wheel", { type: "wheel", deltaY: dy, clientX: x, clientY: y, deltaMode: 0, timeStamp: performance.now() });
    await wait(rnd(40, 140));
    fire("scroll", { type: "scroll", scrollY: dy, timeStamp: performance.now() });
    await wait(rnd(40, 120));
  }
  // 逐键敲邮箱(register-kit 对齐: 确定性敲邮箱长度串模拟注册输入, 非随机短字符)。
  // register-kit 硬编码 "user.name2481@icloud.com"; 优先用 payload 注入的注册邮箱, 缺省同类串。
  const emailStr = String(globalThis.__reg_email || "user.name2481@icloud.com");
  for (const ch of emailStr) {
    fire("keydown", { type: "keydown", key: ch, code: "Key" + ch.toUpperCase(), keyCode: ch.charCodeAt(0), which: ch.charCodeAt(0), timeStamp: performance.now() });
    await wait(rnd(30, 90));  // 人类打字节奏
  }
  // 确定性点击(register-kit 对齐: 每轮都点, 保证 so 行为字段完整)
  {
    fire("click", { type: "click", clientX: x + rnd(-10, 10), clientY: y + rnd(-10, 10), button: 0, timeStamp: performance.now() });
    await wait(rnd(50, 140));
  }
  fire("message", { type: "message", timeStamp: performance.now() });
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

  if (payload.action === "collect_test") {
    // 快速测试 collector 异步协调：se + 延迟 → dump __oai_so_*（不跑慢 _n）
    const _flow = String(payload.flow || "");
    try {
      // 模拟 solve 完整流程：getEnforcementToken(产 p，初始化字节码 VM 状态) + D 注册
      if (!payload.skip_fp && globalThis.__debugP && typeof globalThis.__debugP.getEnforcementToken === 'function') {
        try { await globalThis.__debugP.getEnforcementToken(payload.challenge || {}); } catch (e) { /* ignore */ }
      }
      // solve 独有：bindProof(challenge, request_p) —— 可能是 collector 启动关键
      if (typeof globalThis.SentinelSDK !== 'undefined' && typeof globalThis.SentinelSDK.__debug_bindProof === 'function') {
        try { globalThis.SentinelSDK.__debug_bindProof(payload.challenge || {}, String(payload.request_p || "")); } catch (e) { /* ignore */ }
      }
      // se 前注册 D(challenge, request_p)：collector_dx 解密需要 key（solve 里 getEnforcementToken 会做，
      // collect_test 之前漏了 → collector 解密失败不启动 = 字段 null 根因）
      if (typeof globalThis.__debug_D === "function") {
        try { globalThis.__debug_D(payload.challenge || {}, String(payload.request_p || "")); } catch (e) { /* ignore */ }
      }
      // 模拟 solve：_n(t 求解，初始化字节码 VM 状态 St/jt，collector 复用）
      if (!payload.skip_n && globalThis.SentinelSDK && typeof globalThis.SentinelSDK.__debug_n === 'function') {
        const _dx = (payload.challenge || {}).turnstile && (payload.challenge || {}).turnstile.dx;
        if (_dx) { try { await globalThis.SentinelSDK.__debug_n(payload.challenge || {}, _dx); } catch (e) { /* ignore */ } }
      }
      if (!payload.skip_se && typeof globalThis.__debug_se === "function") globalThis.__debug_se(_flow, payload.challenge || {});
      // 让 collector 注册监听器（其 jt 异步，需 yield）
      await new Promise((r) => setTimeout(r, Number(payload.se_wait_ms || 500)));
      // dump 初始化状态（se 后无事件，collector 是否初始化字段）
      const oai_before = {};
      for (const k of Object.keys(globalThis)) {
        if (k.startsWith("__oai_so_")) oai_before[k] = String(globalThis[k]).slice(0, 60);
      }
      // 手动写字段测试：验证 vm 里 globalThis.__oai_so_* 可写性（字段写入失败根因）
      try { Reflect.set(globalThis, "__oai_so_test", 123); globalThis.__write_test = "OK:" + String(globalThis.__oai_so_test); }
      catch (e) { globalThis.__write_test = "ERR:" + String(e); }
      // 模拟 solve：调 sessionObserverToken，验证是否触发 collector 完整执行（字段初始化）
      if (!payload.skip_snapshot && typeof globalThis.SentinelSDK !== 'undefined' && typeof globalThis.SentinelSDK.sessionObserverToken === 'function') {
        try { await globalThis.SentinelSDK.sessionObserverToken(_flow); } catch (e) { globalThis.__sot_err = String(e); }
      }
      const oai_after_snap = {};
      for (const k of Object.keys(globalThis)) {
        if (k.startsWith('__oai_so_')) oai_after_snap[k] = String(globalThis[k]).slice(0, 60);
      }
      if (payload.skip_events) return { oai_before, oai_after: oai_after_snap, event_log: Array.isArray(globalThis.__event_log) ? globalThis.__event_log : [],
               write_test: globalThis.__write_test, oai_test: globalThis.__oai_so_test, sot_err: globalThis.__sot_err || null };
      // 真实间隔事件：yield 让 collector 处理，事件到达已注册的监听器
      const fire = globalThis.__fire_event;
      if (typeof fire === "function") {
        for (let i = 0; i < 8; i++) {
          fire("pointermove", { type: "pointermove", clientX: 200 + i * 50, clientY: 150 + i * 30, pointerType: "mouse", buttons: 0, timeStamp: performance.now() });
          await new Promise((r) => setTimeout(r, 120));
        }
        fire("scroll", { type: "scroll", scrollY: 200, timeStamp: performance.now() });
        await new Promise((r) => setTimeout(r, 100));
        fire("keydown", { type: "keydown", key: "Tab", code: "Tab", keyCode: 9, timeStamp: performance.now() });
        await new Promise((r) => setTimeout(r, 100));
        fire("wheel", { type: "wheel", deltaY: 200, clientX: 400, clientY: 300, timeStamp: performance.now() });
        await new Promise((r) => setTimeout(r, 100));
        fire("click", { type: "click", clientX: 500, clientY: 400, timeStamp: performance.now() });
      }
      await new Promise((r) => setTimeout(r, 300));
      const oai = {};
      for (const k of Object.keys(globalThis)) {
        if (k.startsWith("__oai_so_")) oai[k] = String(globalThis[k]).slice(0, 80);
      }
      return { oai_before, oai_after: oai, event_log: Array.isArray(globalThis.__event_log) ? globalThis.__event_log : [],
               t_last: globalThis.__T_LAST || null, t_iter: globalThis.__T_ITER || null };
    } catch (e) {
      return { error: String(e && e.message || e) };
    }
  }

  if (payload.action === "sleeptest") {
    // 最小测试：真实异步 setTimeout 是否 resolve
    const d = Number(payload.sleep_ms || 0) || 0;
    const t0 = Date.now();
    await new Promise((r) => setTimeout(r, d));
    return { elapsed: Date.now() - t0, real: d };
  }

  if (payload.action === "solve") {
    const challenge = payload.challenge || {};
    const requestP = String(payload.request_p || "").trim();
    if (!requestP) throw new Error("missing request_p");
    // skip_fp=true 跳过 getEnforcementToken（纯 t 实验不需要 p，省 ~120s）
    const _t0 = Date.now();
    let finalP = "";
    if (!payload.skip_fp) finalP = await globalThis.__debugP.getEnforcementToken(challenge);
    const _t1 = Date.now();
    globalThis.SentinelSDK.__debug_bindProof(challenge, requestP);
    const dx = challenge && challenge.turnstile ? challenge.turnstile.dx : null;
    if (typeof globalThis.__debug_D === "function") globalThis.__debug_D(challenge, requestP);
    const tValue = (!payload.skip_n && dx) ? await globalThis.SentinelSDK.__debug_n(challenge, dx) : null;
    const _t2 = Date.now();
    // [so] se + sessionObserverToken 产真 so（getEnforcementToken 已设寄存器）
    let so = null;
    // skip_so=true 时跳过 sessionObserverToken —— collector 的 jt 死循环会阻塞事件循环、
    // 拖慢 solve 到 ~120s（纯 t 实验用不到 so，先隔离变量）。
    if (payload.skip_so) {
      so = null;
    } else {
    try {
      const _flow = String(payload.flow || "");
      if (typeof globalThis.__debug_se === "function") globalThis.__debug_se(_flow, challenge);
      globalThis.__se_log_after_se = Array.isArray(globalThis.__event_log) ? [...globalThis.__event_log] : [];
      globalThis.__se_oai_after_se = (() => { const o = {}; for (const k of Object.keys(globalThis)) { if (k.startsWith('__oai_so_')) o[k] = String(globalThis[k]).slice(0, 40); } return o; })();
      // so_wait_collector_ms>0：se 后等 collector 完成再 snapshot（隔离共享 St 状态冲突实验）。
      // wrapper 修复后真实定时器可用，collector 死循环不再永久阻塞。
      if (payload.so_wait_collector_ms && Number(payload.so_wait_collector_ms) > 0) {
        await new Promise((r) => setTimeout(r, Number(payload.so_wait_collector_ms)));
      }
      // 行为模拟默认关（opt-in）：DerrickMclean9927 实测 ~21min 死（快于全空基线 ~1h），
      // 半填充行为状态比干净空状态更可疑。
      if (payload.simulate_behavior) await simulateBehavior();
      // 注入 __oai_so_* 行为字段（绕过挂起的 collector；值遵循真实浏览器模板 browser_oai_so_fields.json）
      // patch_oai_so: 绕过 SUBRUN 污染，补 browser 基线字段值（计数=0, 时序=时间戳）
      if (payload.patch_oai_so) {
        try {
          const _now = Date.now();
          const _pn = (typeof performance !== 'undefined' && performance.now) ? performance.now() : _now;
          for (const _k of ['__oai_so_i','__oai_so_k','__oai_so_kp','__oai_so_we','__oai_so_wb','__oai_so_fn',
                             '__oai_so_pc','__oai_so_hc','__oai_so_bc','__oai_so_bm','__oai_so_cn','__oai_so_sn',
                             '__oai_so_st','__oai_so_sw','__oai_so_ht','__oai_so_spt','__oai_so_cs','__oai_so_cs2',
                             '__oai_so_fs','__oai_so_fs2','__oai_so_ss','__oai_so_ss2','__oai_so_sp','__oai_so_wl']) {
            if (globalThis[_k] == null) globalThis[_k] = 0;
          }
          if (globalThis.__oai_so_t0 == null) globalThis.__oai_so_t0 = _now - 60000;
          if (globalThis.__oai_so_s == null) globalThis.__oai_so_s = _pn;
          if (globalThis.__oai_so_m == null) globalThis.__oai_so_m = _pn;
          if (globalThis.__oai_so_p == null) globalThis.__oai_so_p = _pn;
          if (globalThis.__oai_so_fs == null) globalThis.__oai_so_fs = _pn;
          globalThis.__patch_check = String(globalThis.__oai_so_i) + '/' + String(globalThis.__oai_so_k) + '/' + String(globalThis.__oai_so_t0);
        } catch (e) { /* ignore */ }
      }
      // 黑盒探测：极端值注入，测 create 层是否校验字段值范围
      if (payload.snap_extreme) {
        globalThis.__snap_inject = () => {
          globalThis.__oai_so_i = 100000;
          globalThis.__oai_so_k = 99999;
          globalThis.__oai_so_s = 99999999;
          globalThis.__oai_so_cs = 88888888;
          globalThis.__oai_so_t0 = 1;
          globalThis.__oai_so_lx = 1000000;
          globalThis.__oai_so_ly = 1000000;
          globalThis.__oai_so_m = 77777777;
          globalThis.__oai_so_sp = 5000000;
        };
      }
      // 第一性原理：绕过 collector，在 snapshot 读取点注入自然字段值(browser 分布量级+随机化)
      if (payload.snap_inject) {
        globalThis.__snap_inject = () => {
          globalThis.__snap_called = (globalThis.__snap_called || 0) + 1;
          try {
            // 用浏览器真实分布(so_distribution.json)：i 40-60, s 4000-30000, cs 1000-1400,
            // sp 0-400, fs2=fs², ss2=ss², sx0/sy0 屏幕内起点
            const _move = 40 + Math.floor(Math.random() * 20); // i: 42-56
            globalThis.__oai_so_i = _move;
            globalThis.__oai_so_k = Math.floor(Math.random() * 5); // 0-4
            globalThis.__oai_so_kp = 0;
            globalThis.__oai_so_we = Math.random() < 0.5 ? 1 : 0;
            globalThis.__oai_so_wb = Math.random() < 0.5 ? 1 : 0;
            globalThis.__oai_so_s = 4000 + Math.random() * 26000; // 4000-30000
            globalThis.__oai_so_t0 = Date.now() - (30000 + Math.random() * 120000);
            globalThis.__oai_so_m = 5000 + Math.random() * 30000;
            globalThis.__oai_so_p = 2000 + Math.random() * 25000;
            globalThis.__oai_so_cs = 1000 + Math.random() * 400; // 1000-1400
            globalThis.__oai_so_cs2 = globalThis.__oai_so_cs * (50 + Math.random() * 20); // cs2≈59*cs
            globalThis.__oai_so_ss = 20000 + Math.random() * 280000;
            globalThis.__oai_so_ss2 = globalThis.__oai_so_ss * globalThis.__oai_so_ss;
            globalThis.__oai_so_lx = 300 + Math.floor(Math.random() * 500);
            globalThis.__oai_so_ly = 200 + Math.floor(Math.random() * 200);
            globalThis.__oai_so_sx0 = 330 + Math.floor(Math.random() * 90);
            globalThis.__oai_so_sy0 = 280 + Math.floor(Math.random() * 90);
            globalThis.__oai_so_sp = Math.random() * 400;
            globalThis.__oai_so_fs = 6000 + Math.random() * 21000;
            globalThis.__oai_so_fs2 = globalThis.__oai_so_fs * globalThis.__oai_so_fs;
            globalThis.__oai_so_fn = 4;
            globalThis.__oai_so_sn = 35 + Math.floor(Math.random() * 15);
            globalThis.__oai_so_sw = 4;
            globalThis.__oai_so_spt = 1 + Math.floor(Math.random() * 8);
            globalThis.__oai_so_wl = 6000 + Math.random() * 34000;
            globalThis.__oai_so_ht = Math.random() * 3;
            globalThis.__oai_so_pc = Math.random() < 0.5 ? 1 : 0;
            globalThis.__oai_so_hc = Math.random() < 0.8 ? 1 : 0;
            globalThis.__oai_so_cn = 35 + Math.floor(Math.random() * 15);
            globalThis.__oai_so_st = 0;
            globalThis.__oai_so_bc = 0;
            globalThis.__oai_so_bm = 0;
          } catch (e) { /* ignore */ }
        };
      }
      if (payload.inject_oai_so) {
        try {
          const _now = Date.now();
          const _base = _now - 90000;
          // 函数字段（collector 处理器）——浏览器里是函数，这里放无害占位
          const _noop = function () {};
          globalThis.__oai_so_h = _noop; globalThis.__oai_so_hi = _noop;
          globalThis.__oai_so_hp = _noop; globalThis.__oai_so_hw = _noop;
          // 时间戳/坐标（浏览器模板）
          globalThis.__oai_so_t0 = _base;
          globalThis.__oai_so_lx = 700; globalThis.__oai_so_ly = 500;
          globalThis.__oai_so_sx0 = 550; globalThis.__oai_so_sy0 = 425;
          // 浮点时序累加器（浏览器模板量级）
          globalThis.__oai_so_cs = 2055.1; globalThis.__oai_so_cs2 = 524355;
          globalThis.__oai_so_fs = 7737.9; globalThis.__oai_so_fs2 = 59875096;
          globalThis.__oai_so_ht = 1.6; globalThis.__oai_so_m = 8222.6;
          globalThis.__oai_so_p = 7737.9; globalThis.__oai_so_s = 4543.9;
          globalThis.__oai_so_ss = 184173; globalThis.__oai_so_ss2 = 626260169;
          globalThis.__oai_so_wl = 7582.7; globalThis.__oai_so_sp = 167.7;
          // 计数（浏览器模板）
          globalThis.__oai_so_k = 1; globalThis.__oai_so_kp = 0;
          globalThis.__oai_so_we = 2; globalThis.__oai_so_wb = 1;
          globalThis.__oai_so_pc = 1; globalThis.__oai_so_hc = 1;
          globalThis.__oai_so_fn = 1; globalThis.__oai_so_bc = 0; globalThis.__oai_so_bm = 0;
          globalThis.__oai_so_cn = 78; globalThis.__oai_so_sn = 78;
          globalThis.__oai_so_i = 88; globalThis.__oai_so_st = 2;
          globalThis.__oai_so_sw = 8; globalThis.__oai_so_spt = 7;
        } catch (e) { /* ignore */ }
      }
      if (typeof globalThis.SentinelSDK.sessionObserverToken === "function") {
        const s = await globalThis.SentinelSDK.sessionObserverToken(_flow);
        so = (typeof s === "string") ? s : JSON.stringify(s);
      }
    } catch (e) { globalThis.__so_error = (e && e.stack) || String(e); }
    }
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
      ms_fp: _t1 - _t0,
      ms_n: _t2 - _t1,
      se_log_after_se: globalThis.__se_log_after_se || null,
      se_oai_after_se: globalThis.__se_oai_after_se || null,
      so_error: globalThis.__so_n_err || globalThis.__so_error || null,
      so_jt_err: globalThis.__so_jt_err || null,
      so_rej: globalThis.__so_rej || null,
      reflect_err: globalThis.__reflect_err || null,
      t_err: globalThis.__T_ERR || null,
      t_last: globalThis.__T_LAST || null,
      patch_check: globalThis.__patch_check || null,
      snap_called: globalThis.__snap_called || null,
      rt_keys: globalThis.__rt_keys || null,
      xor_full: globalThis.__xor_full || null,
      setref_err: globalThis.__setref_err || null,
      regs: globalThis.__St ? { i: String(globalThis.__St.get(21.49)), lx: String(globalThis.__St.get(43.41)), k: String(globalThis.__St.get(86.2)) } : null,
      key_regs: globalThis.__St ? { k25: String(globalThis.__St.get(25.96)), k97: String(globalThis.__St.get(97.76)), k010: String(globalThis.__St.get(0.10)) } : null,
      at_dump: globalThis.__At_dump || null,
      patch_n: globalThis.__patch_n || null,
      patch_jt: globalThis.__patch_jt || null,
      trace_now: Array.isArray(globalThis.__trace_now) ? globalThis.__trace_now : [],
      trace_rand: Array.isArray(globalThis.__trace_rand) ? globalThis.__trace_rand : [],
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
