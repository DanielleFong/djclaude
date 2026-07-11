#!/usr/bin/env node
/** Pointer-free Rane transport for the stock ChatGPT/Codex Electron renderer. */

import dgram from "node:dgram";
import fs from "node:fs";

const DEFAULT_PORT = 9229;
const DEFAULT_THREAD_TITLE = "Better Cal Sol";
const UDP_PORT = 17685;
const LIVE_MARKER = "/tmp/dj-sol-cdp.enabled";
const RECEIPT_FILE = "/tmp/dj-sol-cdp-receipt.json";
const METRICS_FILE = "/tmp/dj-sol-cdp-metrics.json";
const FLUSH_INTERVAL_MS = 1;
const TARGET_REFRESH_MS = 500;
const METRICS_PUBLISH_MS = 1000;
const MAX_IN_FLIGHT = 1;

class LatencyHistogram {
  constructor({ binWidthMs = 0.05, maximumMs = 200 } = {}) {
    this.binWidthMs = binWidthMs;
    this.maximumMs = maximumMs;
    this.bins = new Uint32Array(Math.ceil(maximumMs / binWidthMs) + 1);
    this.count = 0;
    this.sum = 0;
    this.maximum = 0;
  }

  add(valueMs) {
    if (!Number.isFinite(valueMs) || valueMs < 0) return;
    const index = Math.min(
      this.bins.length - 1,
      Math.floor(valueMs / this.binWidthMs),
    );
    this.bins[index] += 1;
    this.count += 1;
    this.sum += valueMs;
    this.maximum = Math.max(this.maximum, valueMs);
  }

  percentile(fraction) {
    if (this.count === 0) return null;
    const rank = Math.max(1, Math.ceil(this.count * fraction));
    let cumulative = 0;
    for (let index = 0; index < this.bins.length; index += 1) {
      cumulative += this.bins[index];
      if (cumulative >= rank) {
        return Math.min(this.maximumMs, (index + 1) * this.binWidthMs);
      }
    }
    return this.maximum;
  }

  summary() {
    return {
      n: this.count,
      mean_ms: this.count === 0 ? null : this.sum / this.count,
      p50_ms: this.percentile(0.5),
      p95_ms: this.percentile(0.95),
      p99_ms: this.percentile(0.99),
      p99_9_ms: this.percentile(0.999),
      p99_99_ms: this.percentile(0.9999),
      p99_99_qualified: this.count >= 10_000,
      max_ms: this.count === 0 ? null : this.maximum,
      overflow_ms: this.maximumMs,
      resolution_ms: this.binWidthMs,
    };
  }
}

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
    this.socket.addEventListener("close", () => {
      for (const pending of this.pending.values()) {
        pending.reject(new Error("stock renderer connection closed"));
      }
      this.pending.clear();
    });
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
  }

  async evaluate(expression, { awaitPromise = false } = {}) {
    const response = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise,
      returnByValue: true,
    });
    if (response.exceptionDetails) {
      throw new Error(response.exceptionDetails.text ?? "renderer evaluation failed");
    }
    return response.result?.value;
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const request = { id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify(request));
    });
  }

  close() {
    this.socket.close();
  }
}

function transcriptProbeExpression(threadTitle) {
  const encodedTitle = JSON.stringify(threadTitle);
  return `(() => {
    const title = ${encodedTitle};
    const titleMatches = [...document.querySelectorAll("body *")].filter(element => {
      if (!element.matches("span.min-w-0.truncate")) return false;
      if (element.children.length !== 0 || element.textContent?.trim() !== title) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < 100;
    });
    const containers = [...document.querySelectorAll(".thread-scroll-container")];
    return {
      documentTitle: document.title,
      href: location.href,
      titleMatches: titleMatches.length,
      titleMatchDetails: titleMatches.map(element => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          className: element.className,
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        };
      }),
      containerCount: containers.length,
      containers: containers.map(container => ({
        scrollTop: container.scrollTop,
        scrollHeight: container.scrollHeight,
        clientHeight: container.clientHeight,
        className: container.className,
      })),
    };
  })()`;
}

function scrollExpression(threadTitle, pixelDelta) {
  const encodedTitle = JSON.stringify(threadTitle);
  const encodedDelta = JSON.stringify(pixelDelta);
  return `(async () => {
    const title = ${encodedTitle};
    const titleMatches = [...document.querySelectorAll("body *")].filter(element => {
      if (!element.matches("span.min-w-0.truncate")) return false;
      if (element.children.length !== 0 || element.textContent?.trim() !== title) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < 100;
    });
    const containers = [...document.querySelectorAll(".thread-scroll-container")];
    if (titleMatches.length !== 1 || containers.length !== 1) {
      return { status: "identity_mismatch", titleMatches: titleMatches.length, containerCount: containers.length };
    }
    const container = containers[0];
    const before = container.scrollTop;
    container.scrollBy({ top: ${encodedDelta}, behavior: "auto" });
    await new Promise(resolve => requestAnimationFrame(resolve));
    const after = container.scrollTop;
    return {
      status: before === after ? "stalled" : "observed",
      requestedPixels: ${encodedDelta},
      before,
      after,
      delta: after - before,
      scrollHeight: container.scrollHeight,
      clientHeight: container.clientHeight,
    };
  })()`;
}

function wheelTargetExpression(threadTitle) {
  const encodedTitle = JSON.stringify(threadTitle);
  return `(() => {
    const title = ${encodedTitle};
    const titleMatches = [...document.querySelectorAll("body *")].filter(element => {
      if (!element.matches("span.min-w-0.truncate")) return false;
      if (element.children.length !== 0 || element.textContent?.trim() !== title) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < 100;
    });
    const containers = [...document.querySelectorAll(".thread-scroll-container")];
    if (titleMatches.length !== 1 || containers.length !== 1) {
      return {
        status: "identity_mismatch",
        titleMatches: titleMatches.length,
        containerCount: containers.length,
      };
    }
    const container = containers[0];
    const rect = container.getBoundingClientRect();
    return {
      status: "ready",
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      before: container.scrollTop,
      scrollHeight: container.scrollHeight,
      clientHeight: container.clientHeight,
    };
  })()`;
}

function scrollStateExpression(threadTitle) {
  const encodedTitle = JSON.stringify(threadTitle);
  return `(() => {
    const title = ${encodedTitle};
    const titleMatches = [...document.querySelectorAll("span.min-w-0.truncate")]
      .filter(element => {
        if (element.children.length !== 0 || element.textContent?.trim() !== title) return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < 100;
      });
    const containers = [...document.querySelectorAll(".thread-scroll-container")];
    if (titleMatches.length !== 1 || containers.length !== 1) {
      return {
        status: "identity_mismatch",
        titleMatches: titleMatches.length,
        containerCount: containers.length,
      };
    }
    const container = containers[0];
    return {
      status: "ready",
      after: container.scrollTop,
      scrollHeight: container.scrollHeight,
      clientHeight: container.clientHeight,
    };
  })()`;
}

async function dispatchWheel(client, threadTitle, pixelDelta) {
  const target = await client.evaluate(wheelTargetExpression(threadTitle));
  if (target?.status !== "ready") return target;

  await client.send("Input.dispatchMouseEvent", {
    type: "mouseWheel",
    x: target.x,
    y: target.y,
    deltaX: 0,
    deltaY: pixelDelta,
    modifiers: 0,
    pointerType: "mouse",
  });

  // Sample after Chromium has had one display interval to run the stock
  // transcript's wheel and virtualization handlers.
  await new Promise(resolve => setTimeout(resolve, 8));
  const state = await client.evaluate(scrollStateExpression(threadTitle));
  if (state?.status !== "ready") return state;
  return {
    status: target.before === state.after ? "stalled" : "observed",
    requestedPixels: pixelDelta,
    before: target.before,
    after: state.after,
    delta: state.after - target.before,
    scrollHeight: state.scrollHeight,
    clientHeight: state.clientHeight,
    input: "cdp_mouseWheel",
  };
}

async function dispatchWheelAtTarget(client, target, pixelDelta) {
  await client.send("Input.dispatchMouseEvent", {
    type: "mouseWheel",
    x: target.x,
    y: target.y,
    deltaX: 0,
    deltaY: pixelDelta,
    modifiers: 0,
    pointerType: "mouse",
  });
}

function seekExpression(threadTitle, fraction) {
  const encodedTitle = JSON.stringify(threadTitle);
  const encodedFraction = JSON.stringify(Math.max(0, Math.min(1, fraction)));
  return `(async () => {
    const title = ${encodedTitle};
    const titleMatches = [...document.querySelectorAll("body *")].filter(element => {
      if (!element.matches("span.min-w-0.truncate")) return false;
      if (element.children.length !== 0 || element.textContent?.trim() !== title) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < 100;
    });
    const containers = [...document.querySelectorAll(".thread-scroll-container")];
    if (titleMatches.length !== 1 || containers.length !== 1) {
      return { status: "identity_mismatch", titleMatches: titleMatches.length, containerCount: containers.length };
    }
    const container = containers[0];
    const before = container.scrollTop;
    const minimum = Math.min(0, container.clientHeight - container.scrollHeight);
    const target = minimum * (1 - ${encodedFraction});
    container.scrollTo({ top: target, behavior: "auto" });
    await new Promise(resolve => requestAnimationFrame(resolve));
    return {
      status: before === container.scrollTop ? "stalled" : "observed",
      fraction: ${encodedFraction},
      before,
      after: container.scrollTop,
      delta: container.scrollTop - before,
      minimum,
      scrollHeight: container.scrollHeight,
      clientHeight: container.clientHeight,
    };
  })()`;
}

async function pageTargets(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json/list`);
  if (!response.ok) throw new Error(`CDP target list failed: ${response.status}`);
  return (await response.json()).filter(target => target.type === "page");
}

async function findThreadRenderer(port, threadTitle) {
  const candidates = [];
  for (const target of await pageTargets(port)) {
    const client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();
    const probe = await client.evaluate(transcriptProbeExpression(threadTitle));
    if (probe?.titleMatches === 1 && probe?.containerCount === 1) {
      return { client, probe, target };
    }
    candidates.push({ probe, target: { id: target.id, title: target.title, url: target.url } });
    client.close();
  }
  throw new Error(`exact stock task renderer not found: ${JSON.stringify(candidates)}`);
}

async function runProbe(port, threadTitle) {
  const targets = await pageTargets(port);
  const results = [];
  for (const target of targets) {
    const client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();
    results.push({
      target: { id: target.id, title: target.title, url: target.url },
      probe: await client.evaluate(transcriptProbeExpression(threadTitle)),
    });
    client.close();
  }
  console.log(JSON.stringify(results, null, 2));
}

async function runOnce(port, threadTitle, pixels) {
  const { client, probe, target } = await findThreadRenderer(port, threadTitle);
  const receipt = await dispatchWheel(client, threadTitle, pixels);
  console.log(JSON.stringify({ probe, receipt, target: target.id }, null, 2));
  client.close();
}

function parseArguments(argv) {
  const options = {
    mode: "bridge",
    pixels: null,
    port: DEFAULT_PORT,
    threadTitle: DEFAULT_THREAD_TITLE,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--probe") options.mode = "probe";
    else if (value === "--once") {
      options.mode = "once";
      options.pixels = Number(argv[++index]);
    } else if (value === "--port") options.port = Number(argv[++index]);
    else if (value === "--thread-title") options.threadTitle = argv[++index];
  }
  return options;
}

const options = parseArguments(process.argv.slice(2));
if (options.mode === "probe") {
  await runProbe(options.port, options.threadTitle);
} else if (options.mode === "once") {
  await runOnce(options.port, options.threadTitle, options.pixels);
} else {
  const { client } = await findThreadRenderer(options.port, options.threadTitle);
  client.socket.addEventListener("close", () => process.exit(2));
  const socket = dgram.createSocket("udp4");
  let pendingPixels = 0;
  let pendingSourceUnixNs = null;
  let pendingReceivedAtNs = null;
  let pendingFraction = null;
  let flushTimer = null;
  let inFlight = 0;
  let seekInFlight = false;
  let target = null;
  let rendererPosition = null;
  let targetRefreshInFlight = false;
  let lastReceipt = { status: "starting" };
  const sourceToAck = new LatencyHistogram();
  const bridgeIngressToAck = new LatencyHistogram();
  const cdpAck = new LatencyHistogram();

  const recordReceipt = receipt => {
    lastReceipt = receipt;
  };

  const scheduleFlush = () => {
    if (flushTimer !== null) return;
    flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS);
  };

  const runSeek = fraction => {
    if (seekInFlight) {
      pendingFraction = fraction;
      return;
    }
    seekInFlight = true;
    client.evaluate(
      seekExpression(options.threadTitle, fraction),
      { awaitPromise: true },
    ).then(recordReceipt).catch(error => {
      recordReceipt({ status: "bridge_error", error: String(error) });
    }).finally(() => {
      seekInFlight = false;
      if (pendingFraction !== null) scheduleFlush();
    });
  };

  const flush = () => {
    flushTimer = null;
    const fraction = pendingFraction;
    pendingFraction = null;
    if (fraction !== null) runSeek(fraction);

    if (pendingPixels === 0) return;
    if (target === null || inFlight >= MAX_IN_FLIGHT) {
      scheduleFlush();
      return;
    }

    const pixels = pendingPixels;
    const sourceUnixNs = pendingSourceUnixNs;
    const receivedAtNs = pendingReceivedAtNs;
    pendingPixels = 0;
    pendingSourceUnixNs = null;
    pendingReceivedAtNs = null;
    const dispatchedAtNs = process.hrtime.bigint();
    inFlight += 1;
    dispatchWheelAtTarget(client, target, pixels).then(() => {
      const acknowledgedAtNs = process.hrtime.bigint();
      const ackMs = Number(acknowledgedAtNs - dispatchedAtNs) / 1_000_000;
      cdpAck.add(ackMs);
      if (receivedAtNs !== null) {
        bridgeIngressToAck.add(
          Number(acknowledgedAtNs - receivedAtNs) / 1_000_000,
        );
      }
      if (sourceUnixNs !== null) {
        const acknowledgedUnixNs = BigInt(Math.round(
          (performance.timeOrigin + performance.now()) * 1_000_000,
        ));
        sourceToAck.add(
          Number(acknowledgedUnixNs - sourceUnixNs) / 1_000_000,
        );
      }
      recordReceipt({
        status: "dispatched",
        requestedPixels: pixels,
        input: "cdp_mouseWheel",
        cdpAckMs: ackMs,
      });
    }).catch(error => {
      recordReceipt({ status: "bridge_error", error: String(error) });
      target = null;
    }).finally(() => {
      inFlight -= 1;
      if (pendingFraction !== null || pendingPixels !== 0) scheduleFlush();
    });
  };

  const refreshTarget = async () => {
    if (targetRefreshInFlight) return;
    targetRefreshInFlight = true;
    try {
      const candidate = await client.evaluate(
        wheelTargetExpression(options.threadTitle),
      );
      if (candidate?.status === "ready") {
        target = candidate;
        const minimum = Math.min(0, candidate.clientHeight - candidate.scrollHeight);
        const range = Math.max(1, -minimum);
        rendererPosition = {
          scrollTop: candidate.before,
          minimum,
          fraction: Math.max(0, Math.min(1, (candidate.before - minimum) / range)),
          sampledAt: Date.now() / 1000,
        };
      } else {
        target = null;
        rendererPosition = null;
        recordReceipt(candidate ?? { status: "identity_mismatch" });
      }
    } catch (error) {
      target = null;
      recordReceipt({ status: "bridge_error", error: String(error) });
    } finally {
      targetRefreshInFlight = false;
    }
  };

  const publishMetrics = () => {
    const document = {
      ...lastReceipt,
      measuredAt: Date.now() / 1000,
      threadTitle: options.threadTitle,
      transport: "stock_cdp",
      hotPath: {
        flushIntervalMs: FLUSH_INTERVAL_MS,
        maximumInFlight: MAX_IN_FLIGHT,
        inFlight,
      },
      position: rendererPosition,
      latency: {
        source_to_cdp_ack: sourceToAck.summary(),
        bridge_ingress_to_cdp_ack: bridgeIngressToAck.summary(),
        cdp_dispatch_ack: cdpAck.summary(),
      },
    };
    for (const path of [RECEIPT_FILE, METRICS_FILE]) {
      fs.writeFileSync(`${path}.tmp`, JSON.stringify(document));
      fs.renameSync(`${path}.tmp`, path);
    }
    process.stdout.write(`${JSON.stringify(document)}\n`);
  };

  socket.on("message", message => {
    let event;
    try {
      event = JSON.parse(message.toString("utf8"));
    } catch {
      return;
    }
    if (event.head !== "opus") return;
    if (event.type === "jog" && Number.isFinite(event.delta)) {
      const receivedAtNs = process.hrtime.bigint();
      pendingPixels += event.delta * 4;
      if (pendingReceivedAtNs === null || receivedAtNs < pendingReceivedAtNs) {
        pendingReceivedAtNs = receivedAtNs;
      }
      if (typeof event.unix_ns === "string" && /^\d+$/.test(event.unix_ns)) {
        const sourceUnixNs = BigInt(event.unix_ns);
        if (pendingSourceUnixNs === null || sourceUnixNs < pendingSourceUnixNs) {
          pendingSourceUnixNs = sourceUnixNs;
        }
      }
      scheduleFlush();
    } else if (event.type === "strip" && Number.isFinite(event.value)) {
      pendingFraction = event.value / 127;
      pendingPixels = 0;
      scheduleFlush();
    }
  });

  const touchMarker = () => {
    fs.closeSync(fs.openSync(LIVE_MARKER, "a"));
    const now = new Date();
    fs.utimesSync(LIVE_MARKER, now, now);
  };
  await refreshTarget();
  const targetHeartbeat = setInterval(refreshTarget, TARGET_REFRESH_MS);
  const metricsHeartbeat = setInterval(publishMetrics, METRICS_PUBLISH_MS);
  touchMarker();
  const heartbeat = setInterval(touchMarker, 1000);
  const cleanup = () => {
    clearInterval(heartbeat);
    clearInterval(targetHeartbeat);
    clearInterval(metricsHeartbeat);
    try { fs.unlinkSync(LIVE_MARKER); } catch {}
  };
  process.on("SIGINT", () => { cleanup(); process.exit(0); });
  process.on("SIGTERM", () => { cleanup(); process.exit(0); });
  process.on("exit", cleanup);
  socket.bind(UDP_PORT, "127.0.0.1", () => {
    console.log(
      `stock Codex renderer bridge ready udp=127.0.0.1:${UDP_PORT} ` +
      `flush_ms=${FLUSH_INTERVAL_MS} max_in_flight=${MAX_IN_FLIGHT}`,
    );
  });
}
