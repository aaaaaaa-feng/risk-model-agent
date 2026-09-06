import { afterEach, expect, it, vi } from "vitest";
import { runsApi } from "@/features/runs/api/runsApi";

afterEach(() => vi.unstubAllGlobals());

it("读取超过 5000 条的完整历史事件，并将游标传给后续请求", async () => {
  const first = Array.from({ length: 5000 }, (_, index) => ({ sequence: index + 1 }));
  const fetch = vi.fn().mockImplementation((url: string) => {
    const after = new URL(url, "http://localhost").searchParams.get("after");
    const events = after === "5000" ? [{ sequence: 5001 }] : first;
    return Promise.resolve(
      new Response(JSON.stringify({ events, next_sequence: events.at(-1)?.sequence })),
    );
  });
  vi.stubGlobal("fetch", fetch);
  const result = await runsApi.events("run-1");
  expect(result.events).toHaveLength(5001);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("游标未前进时终止分页，不形成无限请求", async () => {
  const events = Array.from({ length: 5000 }, (_, index) => ({ sequence: index + 1 }));
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ events, next_sequence: 0 })));
  vi.stubGlobal("fetch", fetch);
  await expect(runsApi.events("run-1")).rejects.toMatchObject({ code: "EVENT_CURSOR_INVALID" });
  expect(fetch).toHaveBeenCalledOnce();
});
