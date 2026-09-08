import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Linking } from "react-native";
import { GroceryScreen } from "./GroceryScreen";

const auth = { Authorization: "Bearer test" };
let items: any[], order: any, connected: boolean, available: boolean, failPath: string;
let calls: { path: string; method: string; body: any }[];
beforeEach(() => {
  items = [{ id: "milk", name: "Milk", category: "Dairy", reason: "Usually bought weekly", basis: "measured", status: "running_low" }];
  order = { id: "basket", lines: [{ item_id: "milk", name: "Milk", quantity: 1, unit: "" }], platform: "list", fingerprint: "version-1", status: "draft" };
  connected = true; available = true; failPath = ""; calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, opts: any) => {
    const path = url.split("/v1/grocery")[1]; const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ path, method: opts.method, body });
    if (path === failPath) return { ok: false, json: async () => ({ detail: "Couldn't save that change. Please try again." }) };
    let data: any;
    if (path === "/state") data = { mail_connected: connected, item_count: items.length, shelves: [{ category: "Dairy", items }], pending_orders: order ? [{ id: order.id }] : [] };
    else if (path === "/platforms") data = { platforms: [{ platform: "instacart", available, can_handoff: available }] };
    else if (path === "/items") { const item = { id: body.name.toLowerCase(), ...body }; items.push(item); data = { item_id: item.id }; }
    else if (path === "/basket") { order ??= { id: "basket", lines: [], fingerprint: "v1", platform: "list", status: "draft" }; for (const id of body.item_ids) { if (!order.lines.some((l: any) => l.item_id === id)) order.lines.push({ item_id: id, name: items.find(i => i.id === id).name, quantity: 1 }); } data = { order }; }
    else if (path === "/orders/basket" && opts.method === "PATCH") { order = { ...order, ...body, fingerprint: `${order.fingerprint}-next` }; data = { order }; }
    else if (path === "/orders/basket/handoff") data = { url: "https://www.instacart.com/store/shopping_lists/test", order: { ...order, status: "handed_off" } };
    else if (path === "/orders/basket") data = order;
    else if (path === "/items/milk/out") data = { ok: true };
    else throw new Error(`Unexpected call: ${opts.method} ${path}`);
    return { ok: true, json: async () => structuredClone(data) };
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const show = () => render(<GroceryScreen apiUrl="http://test" auth={auth} onConnect={vi.fn()} onAskNano={vi.fn()} />);

it("reviews the actual list, edits quantities, and opens the handoff without a purchase confirmation", async () => {
  const open = vi.spyOn(Linking, "openURL").mockResolvedValue(undefined);
  show(); fireEvent.click(await screen.findByRole("button", { name: "Review shopping list" }));
  fireEvent.click(screen.getByRole("button", { name: "More Milk" }));
  await waitFor(() => expect(screen.getByLabelText("Quantity for Milk").textContent).toBe("2"));
  fireEvent.click(screen.getByRole("button", { name: "Open in Instacart" }));
  await waitFor(() => expect(open).toHaveBeenCalledWith("https://www.instacart.com/store/shopping_lists/test"));
  expect(calls.find(c => c.path.endsWith("/handoff"))?.body.fingerprint).toBe(order.fingerprint);
  expect(calls.some(c => /\/(confirm|place)$/.test(c.path))).toBe(false);
});

it("adds a new item through the visible form without replacing the existing list", async () => {
  show(); fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
  fireEvent.change(screen.getByLabelText("Item name"), { target: { value: "Coffee" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to shopping list" }));
  await screen.findByRole("button", { name: "More Coffee" });
  expect(order.lines.map((l: any) => l.name)).toEqual(["Milk", "Coffee"]);
  expect(calls.find(c => c.path === "/basket")?.body.append).toBe(true);
});

it("opens product details and marks a product out", async () => {
  show(); fireEvent.click(await screen.findByRole("button", { name: "View Milk" }));
  fireEvent.click(screen.getByRole("button", { name: "I'm out" }));
  await waitFor(() => expect(calls.some(c => c.path === "/items/milk/out" && c.body.out)).toBe(true));
  expect(screen.queryByText(/Confidence:/)).toBeNull();
});

it("retains the form and explains a failed save", async () => {
  failPath = "/items"; show(); fireEvent.click(await screen.findByRole("button", { name: "Add item" }));
  fireEvent.change(screen.getByLabelText("Item name"), { target: { value: "Coffee" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to shopping list" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Couldn't save");
  expect((screen.getByLabelText("Item name") as HTMLInputElement).value).toBe("Coffee");
});

it("doesn't ask a connected user to connect again or offer an unavailable store", async () => {
  items = []; order = null; available = false; show();
  await screen.findByText(/Nano is looking for grocery receipts/);
  expect(screen.queryByRole("button", { name: "Connect email" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open in Instacart" })).toBeNull();
});

it("keeps a working connect action for users without a mailbox", async () => {
  items = []; order = null; connected = false; const connect = vi.fn();
  render(<GroceryScreen apiUrl="http://test" auth={auth} onConnect={connect} onAskNano={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect email" }));
  expect(connect).toHaveBeenCalledOnce();
});

it("removes an item and disables store handoff when the list becomes empty", async () => {
  show(); fireEvent.click(await screen.findByRole("button", { name: "Review shopping list" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove Milk" }));
  await screen.findByText("Your list is empty.");
  expect(screen.getByRole("button", { name: "Open in Instacart" }).getAttribute("aria-disabled")).toBe("true");
});

it("opens a review action received during initial loading", async () => {
  render(<GroceryScreen apiUrl="http://test" auth={auth} action={{seq: 1, kind: "review", id: "basket"}} onConnect={vi.fn()} onAskNano={vi.fn()} />);
  await screen.findByRole("button", { name: "More Milk" });
});

it("keeps the list after a store error and allows retry", async () => {
  const open = vi.spyOn(Linking, "openURL").mockResolvedValue(undefined);
  failPath = "/orders/basket/handoff";
  show(); fireEvent.click(await screen.findByRole("button", { name: "Review shopping list" }));
  fireEvent.click(screen.getByRole("button", { name: "Open in Instacart" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "More Milk" })).toBeTruthy();
  expect(open).not.toHaveBeenCalled();
  failPath = "";
  fireEvent.click(screen.getByRole("button", { name: "Open in Instacart" }));
  await waitFor(() => expect(open).toHaveBeenCalledOnce());
});
