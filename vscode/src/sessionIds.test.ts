import { strict as assert } from "node:assert";
import { describe, test } from "node:test";

import { nextSessionName, validateSessionId } from "./sessionIds";

describe("validateSessionId", () => {
  test("accepts schema-compatible ids", () => {
    assert.equal(validateSessionId("main"), null);
    assert.equal(validateSessionId("model-a"), null);
    assert.equal(validateSessionId("9abc"), null);
    assert.equal(validateSessionId("model_a-2"), null);
  });

  test("rejects future remote prefixes and whitespace", () => {
    assert.match(validateSessionId("host-7:main") ?? "", /\[A-Za-z0-9_-\]\+/);
    assert.match(validateSessionId("my session") ?? "", /\[A-Za-z0-9_-\]\+/);
    assert.match(validateSessionId("") ?? "", /\[A-Za-z0-9_-\]\+/);
  });
});

describe("nextSessionName", () => {
  test("returns the first unused numbered session", () => {
    assert.equal(nextSessionName(new Set(["main"])), "session1");
    assert.equal(nextSessionName(new Set(["main", "session1"])), "session2");
  });
});
