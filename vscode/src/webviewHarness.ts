// Runs the .dta viewer end to end outside VS Code: the real session
// (dtaEditor.ts) in this process, the real webview page (media/dtaViewer.js)
// in a headless Chrome, and the two joined the way VS Code joins them.
//
//   page  --postMessage-->  a DevTools binding   --> session.onMessage
//   page  <--window.postMessage--  Runtime.evaluate  <-- webview.postMessage
//
// The page is the HTML the session itself builds, served from a local port
// under its own Content-Security-Policy; `acquireVsCodeApi` is the only thing
// supplied from outside. Chrome is driven over the DevTools protocol on
// Node's built-in WebSocket, so the harness adds no dependency.
//
// Used by dtaWebview.e2e.ts (`npm run test:webview`). `vscode` is not
// imported here: the session is loaded against a stand-in, as in
// dtaEditor.test.ts.

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import Module from "node:module";
import * as os from "node:os";
import * as path from "node:path";

const VSCODE_DIR = path.join(__dirname, "..");
const MEDIA = path.join(VSCODE_DIR, "media");
const FIXTURES = path.join(VSCODE_DIR, "test-fixtures", "dta");

/** A Chrome or Chromium to drive, or undefined when none is installed. */
export function findChrome(): string | undefined {
  const fromEnv = process.env.CHROME_PATH;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  const fixed = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ];
  for (const candidate of fixed) if (fs.existsSync(candidate)) return candidate;
  if (process.platform === "win32") return undefined;
  for (const name of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    const found = spawnSync("which", [name], { encoding: "utf8" });
    if (found.status === 0 && found.stdout.trim()) return found.stdout.trim();
  }
  return undefined;
}

interface Pending {
  resolve: (value: Record<string, unknown>) => void;
  reject: (reason: Error) => void;
}

interface CdpEvent {
  method: string;
  params: Record<string, unknown>;
  sessionId?: string;
}

/** One headless Chrome and a DevTools connection to it. */
export class Browser {
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly listeners = new Set<(event: CdpEvent) => void>();

  private constructor(
    private readonly child: ChildProcess,
    private readonly socket: WebSocket,
    private readonly profile: string,
  ) {
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data)) as {
        id?: number;
        result?: Record<string, unknown>;
        error?: { message: string };
        method?: string;
        params?: Record<string, unknown>;
        sessionId?: string;
      };
      if (message.id !== undefined) {
        const waiting = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (!waiting) return;
        if (message.error) waiting.reject(new Error(message.error.message));
        else waiting.resolve(message.result ?? {});
        return;
      }
      if (message.method) {
        const event: CdpEvent = {
          method: message.method,
          params: message.params ?? {},
          sessionId: message.sessionId,
        };
        for (const listener of this.listeners) listener(event);
      }
    });
  }

  static async launch(chromePath: string): Promise<Browser> {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), "dta-webview-"));
    const args = [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--window-size=1400,900",
    ];
    // A container or a CI runner has no user namespace for Chrome's sandbox.
    if (process.env.CI || process.getuid?.() === 0) args.push("--no-sandbox");
    const child = spawn(chromePath, [...args, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    const endpoint = await new Promise<string>((resolve, reject) => {
      let seen = "";
      const timer = setTimeout(
        () => reject(new Error(`Chrome did not open a DevTools port within 30 s: ${seen.slice(-400)}`)),
        30000,
      );
      child.stderr?.on("data", (chunk: Buffer) => {
        seen += chunk.toString("utf8");
        const m = /DevTools listening on (ws:\/\/\S+)/.exec(seen);
        if (m) {
          clearTimeout(timer);
          resolve(m[1]);
        }
      });
      child.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Chrome exited with code ${code} before it was ready: ${seen.slice(-400)}`));
      });
    });
    const socket = new WebSocket(endpoint);
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener("open", () => resolve(), { once: true });
      socket.addEventListener("error", () => reject(new Error("could not connect to Chrome")), {
        once: true,
      });
    });
    return new Browser(child, socket, profile);
  }

  send(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string,
  ): Promise<Record<string, unknown>> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  on(listener: (event: CdpEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async close(): Promise<void> {
    for (const waiting of this.pending.values()) waiting.reject(new Error("browser closed"));
    this.pending.clear();
    this.socket.close();
    const gone = new Promise<void>((resolve) => this.child.once("exit", () => resolve()));
    this.child.kill();
    await Promise.race([gone, new Promise((resolve) => setTimeout(resolve, 5000))]);
    fs.rmSync(this.profile, { recursive: true, force: true, maxRetries: 5 });
  }
}

// ── the session side ────────────────────────────────────────────────────────

function makeUri(fsPath: string): Record<string, unknown> {
  return { scheme: "file", fsPath, path: fsPath, toString: () => `file://${fsPath}` };
}

/** What the stand-in dialogs answer and what they were asked. */
export const dialogs: { warning?: string; open?: string; asked: string[] } = { asked: [] };
/** Settings the session reads, keyed without the `stataCode.` prefix. */
export const settings = new Map<string, unknown>();

const fakeVscode = {
  Uri: {
    file: makeUri,
    joinPath: (base: { fsPath: string }, ...parts: string[]) => makeUri(path.join(base.fsPath, ...parts)),
  },
  RelativePattern: class {
    constructor(
      readonly base: unknown,
      readonly pattern: string,
    ) {}
  },
  workspace: {
    getConfiguration: () => ({
      get: <T>(key: string, fallback: T): T => (settings.has(key) ? (settings.get(key) as T) : fallback),
    }),
    onDidChangeConfiguration: () => ({ dispose: () => undefined }),
    createFileSystemWatcher: () => ({
      onDidChange: () => ({ dispose: () => undefined }),
      onDidCreate: () => ({ dispose: () => undefined }),
      dispose: () => undefined,
    }),
    workspaceFolders: undefined,
  },
  window: {
    showWarningMessage: async (message: string, options: { detail?: string }) => {
      dialogs.asked.push(`${message} | ${options?.detail ?? ""}`);
      return dialogs.warning;
    },
    showOpenDialog: async () => (dialogs.open ? [makeUri(dialogs.open)] : undefined),
  },
  commands: { executeCommand: async () => undefined },
  env: { clipboard: { writeText: async () => undefined } },
  ProgressLocation: { Notification: 15 },
};

type Loader = (request: string, parent: unknown, isMain: boolean) => unknown;
type EditorModule = typeof import("./dtaEditor");
let editorModule: EditorModule | undefined;

function loadEditor(): EditorModule {
  if (editorModule) return editorModule;
  const internals = Module as unknown as { _load: Loader };
  const realLoad = internals._load;
  internals._load = (request, parent, isMain) =>
    request === "vscode" ? fakeVscode : realLoad(request, parent, isMain);
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    editorModule = require("./dtaEditor") as EditorModule;
  } finally {
    internals._load = realLoad;
  }
  return editorModule;
}

const SHIM = `
  window.acquireVsCodeApi = () => {
    let state;
    return {
      postMessage: (message) => window.__toHost(JSON.stringify(message)),
      getState: () => state,
      setState: (next) => { state = next; },
    };
  };
`;

export interface LiveViewer {
  /** The temporary copy of the fixture the viewer has open. */
  file: string;
  /** Messages the session posted to the page, oldest first. */
  posted: Array<{ type: string; [key: string]: unknown }>;
  /** Evaluate an expression in the page and return its value. */
  evaluate<T>(expression: string): Promise<T>;
  /** Poll `expression` in the page until it is truthy; returns its value. */
  waitFor<T>(expression: string, what?: string, timeoutMs?: number): Promise<T>;
  close(): Promise<void>;
}

/** Open a copy of a test fixture in the viewer, in a new tab of `browser`. */
export async function openViewer(browser: Browser, fixture: string): Promise<LiveViewer> {
  const { DtaViewerProvider } = loadEditor();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dta-viewer-"));
  const file = path.join(dir, fixture);
  fs.copyFileSync(path.join(FIXTURES, fixture), file);

  let html = "";
  const server = http.createServer((request, response) => {
    const url = request.url ?? "/";
    if (url === "/") {
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      response.end(html);
      return;
    }
    const asset = /^\/media\/(dtaViewer\.(js|css))$/.exec(url);
    if (asset) {
      response.writeHead(200, {
        "content-type": asset[2] === "js" ? "text/javascript; charset=utf-8" : "text/css; charset=utf-8",
      });
      response.end(fs.readFileSync(path.join(MEDIA, asset[1])));
      return;
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;

  const { targetId } = (await browser.send("Target.createTarget", { url: "about:blank" })) as {
    targetId: string;
  };
  const { sessionId } = (await browser.send("Target.attachToTarget", {
    targetId,
    flatten: true,
  })) as { sessionId: string };
  const call = (method: string, params: Record<string, unknown> = {}) =>
    browser.send(method, params, sessionId);

  const posted: LiveViewer["posted"] = [];
  let receive: (message: unknown) => void = () => undefined;
  let dispose: () => void = () => undefined;
  let pageReady = false;
  const backlog: unknown[] = [];
  let outbox: Promise<unknown> = Promise.resolve();
  const deliver = (message: unknown): void => {
    outbox = outbox
      .then(() =>
        call("Runtime.evaluate", {
          expression: `window.postMessage(${JSON.stringify(message)}, "*")`,
        }),
      )
      .catch(() => undefined); // the tab is closing
  };

  const stop = browser.on((event) => {
    if (event.sessionId !== sessionId || event.method !== "Runtime.bindingCalled") return;
    if (event.params.name !== "__toHost") return;
    if (!pageReady) {
      pageReady = true;
      for (const message of backlog.splice(0)) deliver(message);
    }
    receive(JSON.parse(String(event.params.payload)));
  });

  const webview = {
    options: {},
    set html(value: string) {
      html = value;
    },
    get html(): string {
      return html;
    },
    cspSource: origin,
    asWebviewUri: (uri: { fsPath: string }) => ({ toString: () => `/media/${path.basename(uri.fsPath)}` }),
    postMessage: async (message: { type: string }) => {
      posted.push(message);
      if (pageReady) deliver(message);
      else backlog.push(message);
      return true;
    },
    onDidReceiveMessage: (handler: (message: unknown) => void) => {
      receive = handler;
      return { dispose: () => undefined };
    },
  };
  const panel = {
    webview,
    onDidDispose: (handler: () => void) => {
      dispose = handler;
    },
  };
  const provider = new DtaViewerProvider(makeUri(VSCODE_DIR) as never, () => undefined);
  provider.resolveCustomEditor(provider.openCustomDocument(makeUri(file) as never), panel as never);

  await call("Runtime.enable");
  await call("Page.enable");
  await call("Runtime.addBinding", { name: "__toHost" });
  await call("Page.addScriptToEvaluateOnNewDocument", { source: SHIM });
  await call("Page.navigate", { url: `${origin}/` });

  const evaluate = async <T>(expression: string): Promise<T> => {
    const reply = (await call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })) as { result: { value?: unknown }; exceptionDetails?: { text: string; exception?: { description?: string } } };
    if (reply.exceptionDetails) {
      throw new Error(
        `in page: ${reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text}\n${expression}`,
      );
    }
    return reply.result.value as T;
  };

  const waitFor = async <T>(expression: string, what = expression, timeoutMs = 10000): Promise<T> => {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const value = await evaluate<T>(expression);
      if (value) return value;
      if (Date.now() > deadline) {
        const body = await evaluate<string>("document.body ? document.body.innerText.slice(0, 600) : ''");
        throw new Error(`timed out waiting for: ${what}\npage shows:\n${body}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  };

  return {
    file,
    posted,
    evaluate,
    waitFor,
    close: async () => {
      stop();
      dispose();
      await browser.send("Target.closeTarget", { targetId }).catch(() => undefined);
      await new Promise<void>((resolve) => server.close(() => resolve()));
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}
