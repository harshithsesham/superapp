import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Linking, Modal, Platform as NativePlatform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

export type GroceryAction = { seq: number; kind: string; id?: string };
type Item = { id: string; name: string; category: string; status: string; reason: string; basis: string; last_purchased_at: string };
type Line = { item_id: string; name: string; quantity: number; unit: string };
type Order = { id: string; lines: Line[]; fingerprint: string; platform: string; status: string; external_id: string };
type State = { mail_connected: boolean; item_count: number; shelves: { category: string; items: Item[] }[]; pending_orders: { id: string }[] };
type Platform = { platform: string; available: boolean; can_handoff: boolean };
const C = { bg: "#08070E", panel: "#151122", line: "#302A43", text: "#F4F2FA", muted: "#ACA5BE", purple: "#C7B8FF", amber: "#FFD9A0" };

export function GroceryScreen({ apiUrl, auth, action, onConnect, onAskNano }: {
  apiUrl: string; auth: { Authorization: string }; action?: GroceryAction;
  onConnect: () => void; onAskNano: () => void;
}) {
  const [data, setData] = useState<State | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [order, setOrder] = useState<Order | null>(null);
  const [sheet, setSheet] = useState<"add" | "item" | "basket" | null>(null);
  const [item, setItem] = useState<Item | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const handledAction = useRef<number | undefined>(undefined);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const request = useCallback(async (path: string, method = "GET", body?: unknown) => {
    const res = await fetch(`${apiUrl}/v1/grocery${path}`, { method,
      headers: { ...auth, "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const payload = await res.json().catch(() => null);
    if (!res.ok) throw new Error(typeof payload?.detail === "string" ? payload.detail : "Couldn't save that change. Please try again.");
    return payload;
  }, [apiUrl, auth.Authorization]);
  const refresh = useCallback(async () => {
    const [state, caps] = await Promise.all([request("/state"), request("/platforms")]);
    const latest = state.pending_orders[0] ? await request(`/orders/${state.pending_orders[0].id}`) : null;
    if (!alive.current) return;
    setData(state); setPlatforms(caps.platforms); setOrder(latest);
  }, [request]);
  const run = useCallback(async (fn: () => Promise<void>) => {
    if (working.current) return;
    working.current = true; setBusy(true); setError("");
    try { await fn(); } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : "Couldn't finish that. Please try again.");
    } finally { working.current = false; if (alive.current) setBusy(false); }
  }, []);
  useEffect(() => { void run(refresh); }, [refresh, run]);
  useEffect(() => {
    const timer = setInterval(() => void run(refresh), 30000);
    return () => clearInterval(timer);
  }, [refresh, run]);
  useEffect(() => {
    if (!action || !data || busy || handledAction.current === action.seq) return;
    handledAction.current = action.seq;
    if (action.kind === "connect") onConnect();
    if (action.kind === "add") setSheet("add");
    if (action.kind === "review") void run(async () => {
      setOrder(await request(`/orders/${action.id}`)); setSheet("basket");
    });
    if (action.kind === "item") void run(async () => {
      const state = await request("/state");
      const selected = state.shelves.flatMap((s: { items: Item[] }) => s.items).find((i: Item) => i.id === action.id);
      if (selected) { setItem(selected); setSheet("item"); }
    });
  }, [action?.seq, data, busy]);
  const button = (label: string, fn: () => void, secondary = false, disabled = false) => (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={busy || disabled} onPress={fn}
      style={[s.button, secondary && s.secondary, (busy || disabled) && s.disabled]}>
      <Text style={[s.buttonText, secondary && { color: C.text }]}>{label}</Text>
    </Pressable>
  );
  const add = () => void run(async () => {
    const added = await request("/items", "POST", { name: name.trim() });
    await request("/basket", "POST", { item_ids: [added.item_id], append: true });
    await refresh(); setName(""); setSheet("basket");
  });
  const edit = (lines: Line[]) => void run(async () => {
    if (!order) return;
    try {
      const saved = await request(`/orders/${order.id}`, "PATCH", { lines, fingerprint: order.fingerprint });
      setOrder(saved.order);
    } catch (e) { await refresh(); throw e; }
  });
  const openStore = () => void run(async () => {
    if (!order) return;
    let latest = order;
    if (latest.platform !== "instacart") {
      const saved = await request(`/orders/${latest.id}`, "PATCH", {
        lines: latest.lines, fingerprint: latest.fingerprint, platform: "instacart" });
      latest = saved.order; setOrder(latest);
    }
    const result = await request(`/orders/${latest.id}/handoff`, "POST", { fingerprint: latest.fingerprint });
    setOrder(result.order);
    const url = new URL(result.url);
    if (url.protocol !== "https:" || !(url.hostname === "instacart.com" || url.hostname.endsWith(".instacart.com"))) {
      throw new Error("Couldn't open the shopping link. Your list is saved.");
    }
    await Linking.openURL(result.url);
  });
  const instacart = platforms.some(p => p.platform === "instacart" && p.available && p.can_handoff);
  return <View style={s.root}>
    <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={busy} onRefresh={() => void run(refresh)} tintColor={C.purple} />}>
      <Text style={s.title}>Groceries</Text>
      <Text style={s.subtitle}>What you have. What you need next.</Text>
      {error && !sheet ? <Text accessibilityRole="alert" style={s.error}>{error}</Text> : null}
      {!data ? <>{busy ? <ActivityIndicator color={C.purple} /> : button("Try again", () => void run(refresh))}</> : <>
        <View style={s.card}>
          <Text style={s.heading}>Your shopping list</Text>
          <Text style={s.body}>{order?.lines.length ? `${order.lines.length} items ready to review.` : "Tell Nano what you need, or add an item."}</Text>
          {order?.lines.length ? button("Review shopping list", () => setSheet("basket")) : button("Tell Nano", onAskNano)}
          {button("Add item", () => { setName(""); setSheet("add"); }, true)}
        </View>
        <Text style={s.heading}>On your shelf</Text>
        {!data.item_count ? <View style={s.card}>
          <Text style={s.body}>{data.mail_connected ? "Nano is looking for grocery receipts in your email. Your shelf will fill in as they arrive." : "Connect your email so Nano can find your grocery receipts."}</Text>
          {!data.mail_connected ? button("Connect email", onConnect) : null}
        </View> : data.shelves.map(category => <View key={category.category} style={s.card}>
          <Text style={s.category}>{category.category || "Other items"}</Text>
          {category.items.map(i => <Pressable accessibilityRole="button" accessibilityLabel={`View ${i.name}`} key={i.id}
            onPress={() => { setItem(i); setSheet("item"); }} style={s.row}>
            <View style={s.grow}><Text style={s.itemName}>{i.name}</Text>
              <Text style={s.meta}>{i.reason}</Text></View>
            {i.status !== "stocked" ? <Text style={s.badge}>{i.basis === "declared" ? "Out" : "Check stock"}</Text> : null}
          </Pressable>)}
        </View>)}
      </>}
    </ScrollView>
    <Modal visible={!!sheet} transparent animationType="slide" onRequestClose={() => !busy && setSheet(null)}>
      <KeyboardAvoidingView behavior={NativePlatform.OS === "ios" ? "padding" : undefined} style={s.overlay}><View style={s.sheet}>
        <View style={s.sheetHeader}><Text style={s.heading}>{sheet === "basket" ? "Your shopping list" : sheet === "item" ? item?.name : "Add an item"}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close" disabled={busy} onPress={() => setSheet(null)}><Text style={s.close}>Close</Text></Pressable>
        </View>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: 14, paddingBottom: 24 }}>
          {error ? <Text accessibilityRole="alert" style={s.error}>{error}</Text> : null}
          {sheet === "add" ? <>
            <TextInput accessibilityLabel="Item name" placeholder="Milk, eggs, coffee…" placeholderTextColor={C.muted}
              autoFocus value={name} onChangeText={setName} maxLength={120} style={s.input} onSubmitEditing={() => name.trim() && add()} />
            {button("Add to shopping list", add, false, !name.trim())}
          </> : null}
          {sheet === "item" && item ? <>
            <Text style={s.body}>{item.reason}</Text>
            <Text style={s.meta}>{item.basis === "declared" ? "You marked this as out." : ["measured", "estimated"].includes(item.basis) ? "Based on your purchase history." : "This is a rough estimate. More receipts help Nano learn your pace."}</Text>
            {button("Add to shopping list", () => void run(async () => {
              await request("/basket", "POST", { item_ids: [item.id], append: true }); await refresh(); setSheet("basket");
            }))}
            {button("I'm out", () => void run(async () => {
              await request(`/items/${item.id}/out`, "POST", { out: true }); await refresh(); setSheet(null);
            }), true)}
            {button("Bought more", () => void run(async () => {
              await request("/items", "POST", { name: item.name, bought_now: true }); await refresh(); setSheet(null);
            }), true)}
          </> : null}
          {sheet === "basket" ? <>
            <Text style={s.body}>Review quantities here. Choose products, check prices, and pay on Instacart.</Text>
            {order?.lines.map(l => <View key={l.item_id} style={s.listLine}>
              <View style={s.grow}><Text style={s.itemName}>{l.name}</Text>
                <Pressable accessibilityRole="button" accessibilityLabel={`Remove ${l.name}`} disabled={busy} onPress={() => edit(order.lines.filter(x => x.item_id !== l.item_id))}><Text style={s.close}>Remove</Text></Pressable></View>
              <Pressable accessibilityRole="button" accessibilityLabel={`Less ${l.name}`} disabled={busy || l.quantity <= 1} style={s.quantity} onPress={() => edit(order.lines.map(x => x.item_id === l.item_id ? { ...x, quantity: x.quantity - 1 } : x))}><Text style={s.itemName}>−</Text></Pressable>
              <Text accessibilityLabel={`Quantity for ${l.name}`} style={s.itemName}>{l.quantity}</Text>
              <Pressable accessibilityRole="button" accessibilityLabel={`More ${l.name}`} disabled={busy || l.quantity >= 99} style={s.quantity} onPress={() => edit(order.lines.map(x => x.item_id === l.item_id ? { ...x, quantity: x.quantity + 1 } : x))}><Text style={s.itemName}>+</Text></Pressable>
            </View>)}
            {!order?.lines.length ? <Text style={s.body}>Your list is empty.</Text> : null}
            {button("Add another item", () => { setName(""); setSheet("add"); }, true)}
            {instacart ? button("Open in Instacart", openStore, false, !order?.lines.length) : <Text style={s.meta}>Instacart is unavailable right now. Your shopping list stays saved here.</Text>}
          </> : null}
          {busy ? <ActivityIndicator color={C.purple} /> : null}
        </ScrollView>
      </View></KeyboardAvoidingView>
    </Modal>
  </View>;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg }, content: { padding: 24, paddingBottom: 150, gap: 20 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 38, color: C.text },
  subtitle: { color: C.muted, fontSize: 15, marginTop: -12 }, heading: { fontSize: 21, color: C.text, fontWeight: "600", flexShrink: 1 },
  card: { backgroundColor: C.panel, borderColor: C.line, borderWidth: 1, borderRadius: 20, padding: 18, gap: 14 },
  body: { color: C.text, fontSize: 15, lineHeight: 22 }, meta: { color: C.muted, fontSize: 13, lineHeight: 19 },
  category: { color: C.purple, fontSize: 13, fontWeight: "600" }, itemName: { color: C.text, fontSize: 16, lineHeight: 23 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10, borderTopWidth: 1, borderTopColor: C.line },
  grow: { flex: 1 }, badge: { color: C.amber, fontSize: 12 },
  button: { backgroundColor: C.purple, borderRadius: 14, padding: 14, alignItems: "center" },
  secondary: { backgroundColor: "transparent", borderColor: C.line, borderWidth: 1 }, disabled: { opacity: 0.5 },
  buttonText: { color: C.bg, fontSize: 15, fontWeight: "600" }, error: { color: "#FF9DA8", fontSize: 14, lineHeight: 20 },
  overlay: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.65)" },
  sheet: { maxHeight: "85%", backgroundColor: C.panel, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 24, paddingBottom: 34, gap: 22 },
  sheetHeader: { flexDirection: "row", justifyContent: "space-between", gap: 15, alignItems: "center" },
  close: { color: C.purple, fontSize: 14, paddingVertical: 8 }, input: { color: C.text, backgroundColor: C.bg, borderColor: C.line, borderWidth: 1, borderRadius: 14, padding: 15, fontSize: 16 },
  listLine: { flexDirection: "row", alignItems: "center", gap: 14, borderBottomWidth: 1, borderBottomColor: C.line, paddingVertical: 12 },
  quantity: { padding: 10, borderColor: C.line, borderWidth: 1, borderRadius: 10 },
});
