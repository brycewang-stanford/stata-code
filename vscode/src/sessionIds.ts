export const SESSION_ID_PATTERN = "[A-Za-z0-9_-]+";
export const SESSION_ID_RE = /^[A-Za-z0-9_-]+$/;

export function validateSessionId(value: string): string | null {
  return SESSION_ID_RE.test(value)
    ? null
    : `session id must match ${SESSION_ID_PATTERN}`;
}

export function nextSessionName(knownSessionIds: ReadonlySet<string>): string {
  for (let i = 1; i < 1000; i++) {
    const candidate = `session${i}`;
    if (!knownSessionIds.has(candidate)) return candidate;
  }
  return "session";
}
