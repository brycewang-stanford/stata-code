import { strict as assert } from "node:assert";
import * as fs from "node:fs";
import Module from "node:module";
import * as os from "node:os";
import * as path from "node:path";
import { describe, test } from "node:test";

// The viewer session is the glue between the webview and the label editor.
// It needs the `vscode` API, which only exists inside an extension host, so
// this test loads dtaEditor against a stand-in that implements the handful of
// calls the label paths make. The file system and the .dta editor are real.

const FIXTURES = path.join(__dirname, "..", "test-fixtures", "dta");

interface Posted {
  type: string;
  [key: string]: unknown;
}

function makeUri(fsPath: string): Record<string, unknown> {
  return { scheme: "file", fsPath, path: fsPath, toString: () => `file://${fsPath}` };
}

/** What the stand-in dialogs answer, set by each test. */
const answers: { warning?: string; open?: string; warnings: string[] } = { warnings: [] };

const fakeVscode = {
  Uri: {
    file: makeUri,
    joinPath: (base: { fsPath: string }, ...parts: string[]) =>
      makeUri(path.join(base.fsPath, ...parts)),
  },
  RelativePattern: class {
    constructor(
      readonly base: unknown,
      readonly pattern: string,
    ) {}
  },
  workspace: {
    getConfiguration: () => ({ get: <T>(_key: string, fallback: T): T => fallback }),
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
      answers.warnings.push(`${message} | ${options?.detail ?? ""}`);
      return answers.warning;
    },
    showOpenDialog: async () => (answers.open ? [makeUri(answers.open)] : undefined),
  },
  commands: { executeCommand: async () => undefined },
  env: { clipboard: { writeText: async () => undefined } },
  ProgressLocation: { Notification: 15 },
};

type Loader = (request: string, parent: unknown, isMain: boolean) => unknown;
const moduleInternals = Module as unknown as { _load: Loader };
const realLoad = moduleInternals._load;
moduleInternals._load = (request, parent, isMain) =>
  request === "vscode" ? fakeVscode : realLoad(request, parent, isMain);
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { DtaViewerProvider } = require("./dtaEditor") as typeof import("./dtaEditor");
moduleInternals._load = realLoad;

interface Viewer {
  file: string;
  posted: Posted[];
  send: (message: Record<string, unknown>) => Promise<void>;
  init: () => Posted;
  lastNotice: () => Posted;
  close: () => void;
}

async function openViewer(fixture: string): Promise<Viewer> {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dta-editor-"));
  const file = path.join(dir, fixture);
  fs.copyFileSync(path.join(FIXTURES, fixture), file);
  const posted: Posted[] = [];
  let receive: (message: unknown) => void = () => undefined;
  let dispose: () => void = () => undefined;
  const webview = {
    options: {},
    html: "",
    cspSource: "x",
    asWebviewUri: (uri: unknown) => uri,
    postMessage: async (message: Posted) => {
      posted.push(message);
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
  const provider = new DtaViewerProvider(makeUri(path.join(__dirname, "..")) as never, () => {});
  provider.resolveCustomEditor(provider.openCustomDocument(makeUri(file) as never), panel as never);
  // onMessage is async and the event does not await it. An edit ends in a
  // notice (after the reload's init and view), so wait for the reply to go
  // quiet rather than for a fixed time.
  const settle = async (): Promise<void> => {
    let seen = posted.length;
    let quiet = 0;
    for (let i = 0; i < 400 && quiet < 6; i++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
      quiet = posted.length === seen ? quiet + 1 : 0;
      seen = posted.length;
    }
  };
  const send = async (message: Record<string, unknown>): Promise<void> => {
    receive(message);
    await settle();
  };
  await send({ type: "ready" });
  return {
    file,
    posted,
    send,
    init: () => [...posted].reverse().find((m) => m.type === "init") as Posted,
    lastNotice: () => [...posted].reverse().find((m) => m.type === "notice") as Posted,
    close: () => {
      dispose();
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

function variable(init: Posted, name: string): Record<string, unknown> {
  return (init.variables as Array<Record<string, unknown>>).find((v) => v.name === name) as Record<
    string,
    unknown
  >;
}

describe("viewer session: label edits", () => {
  test("an edit is written, shown, and can be undone", async () => {
    const viewer = await openViewer("survey118.dta");
    try {
      assert.equal(viewer.init().canEditLabels, true);
      await viewer.send({
        type: "editLabels",
        edit: { valueLabels: { agree: { "1": "Agree", "2": "Disagree" } }, attach: { score: "agree" } },
      });
      assert.deepEqual(viewer.lastNotice(), {
        type: "notice",
        text: "Defined value label agree; attached agree to score",
        isError: false,
        canUndo: true,
      });
      // the webview was sent the file as it now is
      assert.equal(variable(viewer.init(), "score").valueLabel, "agree");
      assert.deepEqual((viewer.init().valueLabels as Record<string, unknown>).agree, [
        ["1", "Agree"],
        ["2", "Disagree"],
      ]);

      await viewer.send({ type: "setLabel", name: "wage", label: "  Hourly wage " });
      assert.equal(variable(viewer.init(), "wage").label, "Hourly wage");

      await viewer.send({ type: "undoEdit" });
      assert.equal(variable(viewer.init(), "wage").label, "");
      assert.equal(viewer.lastNotice().canUndo, false);
      assert.match(String(viewer.lastNotice().text), /^Undone: /);
      await viewer.send({ type: "undoEdit" });
      assert.equal(variable(viewer.init(), "score").valueLabel, "");
      assert.equal((viewer.init().valueLabels as Record<string, unknown>).agree, undefined);
      await viewer.send({ type: "undoEdit" });
      assert.equal(viewer.lastNotice().text, "Nothing to undo");
    } finally {
      viewer.close();
    }
  });

  test("a refused edit says why and changes nothing", async () => {
    const viewer = await openViewer("survey118.dta");
    try {
      const before = fs.readFileSync(viewer.file);
      await viewer.send({ type: "editLabels", edit: { attach: { city: "yn" } } });
      assert.equal(viewer.lastNotice().isError, true);
      assert.match(String(viewer.lastNotice().text), /Not saved: city: is a string variable/);
      await viewer.send({ type: "editLabels", edit: { valueLabels: { yn: "not a table" } } });
      await viewer.send({ type: "editLabels", edit: 7 });
      assert.ok(fs.readFileSync(viewer.file).equals(before));
    } finally {
      viewer.close();
    }
  });

  test("dropping a value label asks first", async () => {
    const viewer = await openViewer("survey118.dta");
    try {
      answers.warnings = [];
      answers.warning = undefined; // the dialog is dismissed
      await viewer.send({ type: "dropValueLabel", name: "yn" });
      assert.match(answers.warnings[0], /Drop the value label "yn".*detached from female/);
      assert.equal(variable(viewer.init(), "female").valueLabel, "yn");

      answers.warning = "Drop";
      await viewer.send({ type: "dropValueLabel", name: "yn" });
      assert.equal(variable(viewer.init(), "female").valueLabel, "");
      assert.equal(viewer.lastNotice().text, "Dropped value label yn; detached yn from female");
      await viewer.send({ type: "undoEdit" });
      assert.equal(variable(viewer.init(), "female").valueLabel, "yn");
      assert.deepEqual((viewer.init().valueLabels as Record<string, unknown>).yn, [
        ["0", "No"],
        ["1", "Yes"],
      ]);
    } finally {
      viewer.close();
    }
  });

  test("a codebook CSV is imported as one undoable edit", async () => {
    const viewer = await openViewer("survey118.dta");
    try {
      const dir = path.dirname(viewer.file);
      const codebook = path.join(dir, "survey118_codebook.csv");
      fs.writeFileSync(codebook, "name,label,value_label\nwage,Hourly wage,\nscore,Test score,yn\n");
      fs.writeFileSync(
        path.join(dir, "survey118_codebook_value_labels.csv"),
        "value_label,value,text\nyn,0,No\nyn,1,Yes\nyn,.a,Refused\n",
      );
      answers.open = codebook;
      answers.warnings = [];
      answers.warning = "Apply";
      await viewer.send({ type: "importCodebook" });
      assert.match(answers.warnings[0], /^Apply 4 label changes to survey118\.dta\?/);
      assert.match(answers.warnings[0], /read from survey118_codebook_value_labels\.csv/);
      assert.equal(variable(viewer.init(), "wage").label, "Hourly wage");
      assert.equal(variable(viewer.init(), "score").valueLabel, "yn");
      assert.equal((viewer.init().valueLabels as Record<string, unknown[]>).yn.length, 3);

      // importing it again finds nothing to do and does not ask
      answers.warnings = [];
      await viewer.send({ type: "importCodebook" });
      assert.deepEqual(answers.warnings, []);
      assert.match(String(viewer.lastNotice().text), /nothing to change/);

      await viewer.send({ type: "undoEdit" });
      assert.equal(variable(viewer.init(), "wage").label, "");
      assert.equal(variable(viewer.init(), "score").valueLabel, "");

      fs.writeFileSync(codebook, "variable,label\nwage,x\n");
      await viewer.send({ type: "importCodebook" });
      assert.match(String(viewer.lastNotice().text), /Not imported: the codebook has no "name" column/);
    } finally {
      viewer.close();
    }
  });
});
