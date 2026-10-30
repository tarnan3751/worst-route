// Stand-ins for the browser's network (tests only): `stubFetch` answers the client's requests
// and records them, `FakeEventSource` is an EventSource the test sends events through, and
// `fakeView` records what a run draws.
import { vi } from "vitest";
import type { RouteView } from "../run/controls";

export interface Call {
  url: string;
  method: string;
  body: unknown;
  keepalive: boolean;
}

/** Make `fetch` answer with `answer`; the calls, in order. */
export function stubFetch(answer: (call: Call) => Response | Promise<Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const call: Call = {
      url,
      method: init.method ?? "GET",
      body: init.body === undefined ? undefined : JSON.parse(String(init.body)),
      keepalive: init.keepalive ?? false,
    };
    calls.push(call);
    return answer(call);
  });
  return calls;
}

export const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static opened: FakeEventSource[] = [];
  readonly url: string;
  readyState = FakeEventSource.CONNECTING;

  constructor(url: string) {
    super();
    this.url = url;
    FakeEventSource.opened.push(this);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  /** An event from the server. */
  send(name: string, data: unknown): void {
    this.dispatchEvent(new MessageEvent(name, { data: JSON.stringify(data) }));
  }
}

/** Use FakeEventSource for EventSource; the sources opened since. */
export function stubEventSource(): FakeEventSource[] {
  FakeEventSource.opened = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  return FakeEventSource.opened;
}

export function fakeView(): RouteView {
  return {
    clear: vi.fn(),
    setBaseline: vi.fn(),
    setSnapshot: vi.fn(),
    applySplice: vi.fn(),
    setFinal: vi.fn(),
  };
}

/** Let pending promises run. */
export const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));
