// The Flycatcher's home: a conversation with Nano about flights — typed or
// spoken — plus the standing watches and tap-to-book results. Same brain as
// the orb (/v1/voice/converse), so "find me flights" and "watch this route"
// work identically here and by voice.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { NativeModules } from "react-native";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

type ShortlistItem = { title?: string; price?: string; location?: string; url?: string; why?: string };
type FlightTask = {
  id: string; kind: string; instruction: string; status: string;
  result: { summary?: string; shortlist?: ShortlistItem[]; caveats?: string } | null;
};
type Watch = { id: string; instruction: string; target_price: number | null; best_price: number | null };
type Msg =
  | { kind: "text"; role: "user" | "nano"; text: string }
  | { kind: "results"; task: FlightTask };

const GREETING =
  "Where are we flying? Ask me to find flights — “flights from Columbus to " +
  "New York on December 18” — or set a watch: “watch flights to Hyderabad in " +
  "December, tell me when it’s under $900.”";

export function FlightsScreen({
  apiUrl,
  auth,
  onBack,
  onOpenOrb,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onBack: () => void;
  onOpenOrb: () => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([{ kind: "text", role: "nano", text: GREETING }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [watches, setWatches] = useState<Watch[]>([]);
  const shownTasks = useRef<Set<string>>(new Set());
  const activityUp = useRef(false);
  const scroller = useRef<ScrollView>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [wRes, tRes] = await Promise.all([
        fetch(`${apiUrl}/v1/tasks/watches`, { headers: auth }),
        fetch(`${apiUrl}/v1/tasks`, { headers: auth }),
      ]);
      if (!alive.current) return;
      if (wRes.ok) setWatches((await wRes.json()).watches ?? []);
      if (tRes.ok) {
        const all: FlightTask[] = (await tRes.json()).tasks ?? [];
        // Lock-screen presence: a Live Activity while the scout is out.
        try {
          const running = all.find((t) => t.status === "running" || t.status === "queued");
          if (running) {
            NativeModules.NanoWidgetBridge?.startActivity(
              running.instruction.slice(0, 44),
              JSON.stringify({
                status: running.instruction.slice(0, 80),
                stage: running.status === "queued"
                  ? "Queued — the scout picks it up in seconds."
                  : "The scout is out on the open web.",
                steps: ["Queued", "Searching", "Parsing", "Done"],
                stepIndex: running.status === "queued" ? 0 : 1,
              }));
          } else if (activityUp.current) {
            NativeModules.NanoWidgetBridge?.endActivity(JSON.stringify({
              status: "Done — the shortlist is in.",
              stage: "Done — the shortlist is in.",
              steps: ["Queued", "Searching", "Parsing", "Done"],
              stepIndex: 3,
            }));
          }
          activityUp.current = !!running;
        } catch { /* best-effort */ }
        const tasks: FlightTask[] = all.filter(
          (t: FlightTask) => t.kind === "flights" && t.status === "done" && t.result?.shortlist?.length);
        // Newly completed searches drop into the thread as result cards.
        const fresh = tasks.filter((t) => !shownTasks.current.has(t.id));
        if (fresh.length) {
          fresh.forEach((t) => shownTasks.current.add(t.id));
          // On first load, only seed the most recent so old history doesn't flood.
          const toShow = shownTasks.current.size === fresh.length ? fresh.slice(0, 1) : fresh;
          setMsgs((m) => [...m, ...toShow.map((task) => ({ kind: "results" as const, task }))]);
        }
      }
    } catch {
      /* offline — keep what we have */
    }
  }, [apiUrl, auth]);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = setInterval(refresh, 6000);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  useEffect(() => {
    setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 80);
  }, [msgs]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);
    const history = [...msgs, { kind: "text" as const, role: "user" as const, text }];
    setMsgs(history);
    const body = JSON.stringify({
      messages: history
        .filter((m): m is Extract<Msg, { kind: "text" }> => m.kind === "text")
        .slice(-16)
        .map((m) => ({ role: m.role, text: m.text })),
    });
    // One quiet retry: a brief server blip (e.g. a redeploy) shouldn't throw
    // the "couldn't reach the server" line at you on the first hiccup.
    const ask = async () => {
      const res = await fetch(`${apiUrl}/v1/voice/converse`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body,
      });
      if (!res.ok) throw new Error(`converse ${res.status}`);
      return res.json();
    };
    try {
      let data;
      try {
        data = await ask();
      } catch {
        await new Promise((r) => setTimeout(r, 1200));
        data = await ask();
      }
      if (!alive.current) return;
      setMsgs((m) => [...m, { kind: "text", role: "nano", text: data.say ?? "…" }]);
      refresh();
    } catch {
      if (alive.current) {
        setMsgs((m) => [...m, {
          kind: "text", role: "nano",
          text: "I couldn't reach the server just now — try that again in a moment.",
        }]);
      }
    } finally {
      if (alive.current) setSending(false);
    }
  }, [apiUrl, auth, input, msgs, refresh, sending]);

  const stopWatch = useCallback(async (id: string) => {
    try {
      await fetch(`${apiUrl}/v1/tasks/watch/${id}`, { method: "DELETE", headers: auth });
      refresh();
    } catch {
      /* next poll will tell the truth */
    }
  }, [apiUrl, auth, refresh]);

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={70}
    >
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12}>
          <Text style={styles.back}>‹  MY HUB</Text>
        </Pressable>
        <Text style={styles.title}>FLIGHTS</Text>
        <Pressable onPress={onOpenOrb} hitSlop={12}>
          <Text style={styles.mic}>◉</Text>
        </Pressable>
      </View>

      {watches.length > 0 ? (
        <View style={styles.watchStrip}>
          {watches.map((w) => (
            <View key={w.id} style={styles.watch}>
              <View style={{ flex: 1 }}>
                <Text style={styles.watchText} numberOfLines={1}>
                  {w.instruction.replace(/^watch\s+/i, "")}
                </Text>
                <Text style={styles.watchMeta}>
                  {w.best_price != null ? `best $${w.best_price}` : "first check running"}
                  {w.target_price != null ? `  ·  target $${w.target_price}` : ""}
                </Text>
              </View>
              <Pressable onPress={() => stopWatch(w.id)} hitSlop={10}>
                <Text style={styles.stop}>✕</Text>
              </Pressable>
            </View>
          ))}
        </View>
      ) : null}

      <ScrollView
        ref={scroller}
        style={{ flex: 1 }}
        contentContainerStyle={styles.thread}
        keyboardShouldPersistTaps="handled"
      >
        {msgs.map((m, i) =>
          m.kind === "text" ? (
            <View key={i} style={[styles.bubble, m.role === "user" ? styles.mine : styles.nanos]}>
              <Text style={m.role === "user" ? styles.mineText : styles.nanoText}>{m.text}</Text>
            </View>
          ) : (
            <View key={i} style={styles.results}>
              <Text style={styles.resultsHead} numberOfLines={1}>
                ✈  {m.task.instruction}
              </Text>
              {m.task.result?.summary ? (
                <Text style={styles.resultsSummary}>{m.task.result.summary}</Text>
              ) : null}
              {(m.task.result?.shortlist ?? []).slice(0, 6).map((f, j) => (
                <Pressable
                  key={j}
                  style={styles.flight}
                  onPress={() => f.url && Linking.openURL(f.url)}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.flightTitle} numberOfLines={1}>{f.title ?? "Flight"}</Text>
                    <Text style={styles.flightMeta} numberOfLines={1}>{f.location ?? ""}</Text>
                  </View>
                  <Text style={styles.flightPrice}>{f.price ?? ""}  ↗</Text>
                </Pressable>
              ))}
              <Text style={styles.bookHint}>Tap a flight to book it on Google Flights.</Text>
            </View>
          )
        )}
        {sending ? <ActivityIndicator style={{ marginTop: 10 }} color="#C7B8FF" /> : null}
      </ScrollView>

      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          placeholder="Ask about flights…"
          placeholderTextColor="#8A87A3"
          value={input}
          onChangeText={setInput}
          onSubmitEditing={send}
          returnKeyType="send"
          editable={!sending}
        />
        <Pressable
          style={[styles.sendBtn, { opacity: input.trim() ? 1 : 0.4 }]}
          onPress={send}
          disabled={sending || !input.trim()}
        >
          <Text style={styles.sendText}>{sending ? "…" : "Send"}</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#08070E" },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingVertical: 10,
  },
  back: { color: "#8A87A3", fontFamily: "InstrumentSans_600SemiBold", fontSize: 13 },
  title: { color: "#F1EFFA", fontFamily: "InstrumentSans_600SemiBold", fontSize: 13, letterSpacing: 3 },
  mic: { color: "#C7B8FF", fontSize: 18 },
  watchStrip: { paddingHorizontal: 16, gap: 8, marginBottom: 4 },
  watch: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: "#14121E", borderColor: "#232033", borderWidth: 1,
    borderRadius: 12, paddingHorizontal: 12, paddingVertical: 8,
  },
  watchText: { color: "#F1EFFA", fontSize: 13, fontFamily: "InstrumentSans_600SemiBold" },
  watchMeta: { color: "#8A87A3", fontSize: 12, marginTop: 2 },
  stop: { color: "#8A87A3", fontSize: 15, padding: 2 },
  thread: { padding: 16, gap: 10, paddingBottom: 24 },
  bubble: { maxWidth: "86%", borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10 },
  mine: { alignSelf: "flex-end", backgroundColor: "#C7B8FF" },
  nanos: { alignSelf: "flex-start", backgroundColor: "#14121E", borderWidth: 1, borderColor: "#232033" },
  mineText: { color: "#14101F", fontSize: 14, lineHeight: 20 },
  nanoText: { color: "#F1EFFA", fontSize: 14, lineHeight: 20 },
  results: {
    alignSelf: "stretch", backgroundColor: "#14121E", borderWidth: 1,
    borderColor: "#232033", borderRadius: 16, padding: 14,
  },
  resultsHead: { color: "#8A87A3", fontFamily: "InstrumentSans_600SemiBold", fontSize: 11, letterSpacing: 1 },
  resultsSummary: { color: "#B9B4CC", fontSize: 13, lineHeight: 18, marginTop: 8 },
  flight: {
    flexDirection: "row", alignItems: "center", gap: 10,
    borderTopWidth: 1, borderTopColor: "#232033", marginTop: 10, paddingTop: 10,
  },
  flightTitle: { color: "#F1EFFA", fontSize: 14, fontFamily: "InstrumentSans_600SemiBold" },
  flightMeta: { color: "#8A87A3", fontSize: 12, marginTop: 2 },
  flightPrice: { color: "#C7B8FF", fontSize: 14, fontFamily: "InstrumentSans_600SemiBold" },
  bookHint: { color: "#8A87A3", fontSize: 11, marginTop: 10 },
  inputRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 16, paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: "#232033", backgroundColor: "#0C0B14",
  },
  input: {
    flex: 1, color: "#F1EFFA", fontSize: 15,
    backgroundColor: "#14121E", borderColor: "#232033", borderWidth: 1,
    borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10,
  },
  sendBtn: { backgroundColor: "#C7B8FF", borderRadius: 999, paddingHorizontal: 18, paddingVertical: 10 },
  sendText: { color: "#14101F", fontFamily: "InstrumentSans_600SemiBold", fontSize: 14 },
});
