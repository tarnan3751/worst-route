import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";
import { json, stubFetch } from "../__fixtures__/http";
import { ApiError, errorBody, getRegions } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The ApiError `promise` rejects with. */
async function refusal(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => null,
    (e: unknown) => e,
  );
  if (!(error instanceof ApiError)) throw new Error(`not an ApiError: ${error}`);
  return error;
}

describe("the client's errors", () => {
  it("passes on the API's own body", async () => {
    const body = { code: "STOP_OUTSIDE_REGION", message: "outside", stop_index: 1 };
    stubFetch(() => json(body, 422));
    const error = await refusal(getRegions());
    expect(error.status).toBe(422);
    expect(error.body).toEqual(body);
  });

  it("names a success that is not JSON BAD_RESPONSE, with the start of what came", async () => {
    const page = `<!doctype html><html><head><title>Worst Route</title></head><body>${"x".repeat(200)}`;
    stubFetch(() => new Response(page, { status: 200, headers: { "Content-Type": "text/html" } }));
    const error = await refusal(getRegions());
    expect(error.body).toEqual({ code: "BAD_RESPONSE", message: page.slice(0, 80) });
    expect(error.body.message).toHaveLength(80);
  });

  it("names a refusal without the API's body by its status, and no server NETWORK", async () => {
    stubFetch(() => new Response("Bad Gateway", { status: 502 }));
    expect((await refusal(getRegions())).body).toEqual({
      code: "HTTP_502",
      message: "Bad Gateway",
    });
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("fetch failed")));
    expect((await refusal(getRegions())).body.code).toBe("NETWORK");
  });

  it("shows any other error as CLIENT_ERROR", () => {
    const api = new ApiError(404, { code: "NOT_FOUND", message: "no route x" });
    expect(errorBody(api)).toEqual(api.body);
    expect(errorBody(new TypeError("x is undefined"))).toEqual({
      code: "CLIENT_ERROR",
      message: "x is undefined",
    });
    expect(errorBody("odd")).toEqual({ code: "CLIENT_ERROR", message: "odd" });
  });
});
