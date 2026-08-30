// The scout's home in the app: kick off a find-it errand and see what it
// found, tappable. No account logins — the scout works the open web
// (resale marketplaces, retailers) read-only, so nothing puts your
// accounts at risk.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

type ShortlistItem = {
  title?: string;
  price?: string;
  location?: string;
  url?: string;
  why?: string;
};

type ScoutTask = {
  id: string;
  kind: string;
  instruction: string;
  status: string;
  result: { summary?: string; shortlist?: ShortlistItem[]; caveats?: string } | null;
  error: string | null;
  created_at: string;
};

export function ScoutCard({
  apiUrl,
  auth,
  dark,
  onOpenFlights,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  dark: boolean;
  onOpenFlights?: () => void;
}) {
  const [tasks, setTasks] = useState<ScoutTask[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const alive = useRef(true);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/v1/tasks`, { headers: auth });
      if (!res.ok) return;
      const data = await res.json();
      if (!alive.current) return;
      setTasks(data.tasks ?? []);
    } catch {
      /* offline — keep last known */
    }
  }, [apiUrl, auth]);

  useEffect(() => {
    alive.current = true;
    fetchTasks();
    const timer = setInterval(fetchTasks, 20000);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [fetchTasks]);

  const sendErrand = useCallback(async () => {
    const instruction = draft.trim();
    if (instruction.length < 8 || sending) return;
    setSending(true);
    try {
      const res = await fetch(`${apiUrl}/v1/tasks`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ instruction, kind: "research" }),
      });
      if (!res.ok) throw new Error("queue failed");
      setDraft("");
      // Give the poller a beat to flip it to running.
      setTimeout(fetchTasks, 1500);
      await fetchTasks();
    } catch {
      Alert.alert("Couldn't send that errand", "Give it a minute and try again.");
    } finally {
      if (alive.current) setSending(false);
    }
  }, [apiUrl, auth, draft, fetchTasks, sending]);

  const c = dark ? palette.dark : palette.light;
  const errands = tasks.filter((t) => t.kind !== "connect_login").slice(0, 3);

  return (
    <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
      <View style={styles.headerRow}>
        <Text style={[styles.label, { color: c.muted }]}>SCOUT</Text>
        {onOpenFlights ? (
          <Pressable onPress={onOpenFlights} hitSlop={8}>
            <Text style={[styles.flightsLink, { color: c.accentText }]}>✈  Flights  ›</Text>
          </Pressable>
        ) : null}
      </View>
      <Text style={[styles.sub, { color: c.muted }]}>
        Send it to find something on the open web — resale sites and retailers.
        Read-only, no logins.
      </Text>

      <View style={[styles.inputRow, { borderColor: c.border }]}>
        <TextInput
          style={[styles.input, { color: c.text }]}
          placeholder="find a used standing desk under $150"
          placeholderTextColor={c.muted}
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={sendErrand}
          returnKeyType="send"
          editable={!sending}
        />
        <Pressable
          style={[styles.sendBtn, { backgroundColor: c.accent, opacity: draft.trim().length < 8 ? 0.4 : 1 }]}
          onPress={sendErrand}
          disabled={sending || draft.trim().length < 8}
        >
          <Text style={[styles.sendText, { color: c.onAccent }]}>{sending ? "…" : "Find"}</Text>
        </Pressable>
      </View>

      {errands.length === 0 ? (
        <Text style={[styles.hint, { color: c.muted }]}>
          Or just ask the orb: “find a used desk under $150”.
        </Text>
      ) : null}

      {errands.map((t) => (
        <View key={t.id} style={[styles.task, { borderTopColor: c.border }]}>
          <View style={styles.taskHeader}>
            <Text style={[styles.status, statusColor(t.status, c)]}>
              {t.status.toUpperCase()}
            </Text>
            <Text style={[styles.instruction, { color: c.text }]} numberOfLines={1}>
              {t.instruction}
            </Text>
          </View>
          {t.result?.summary ? (
            <Text style={[styles.summary, { color: c.muted }]} numberOfLines={3}>
              {t.result.summary}
            </Text>
          ) : null}
          {(t.result?.shortlist ?? []).slice(0, 6).map((item, i) => (
            <Pressable
              key={i}
              style={styles.item}
              onPress={() => item.url && Linking.openURL(item.url)}
            >
              <Text style={[styles.itemTitle, { color: c.text }]} numberOfLines={1}>
                {item.title ?? "Listing"}
              </Text>
              <Text style={[styles.itemMeta, { color: c.accentText }]}>
                {[item.price, item.location].filter(Boolean).join(" · ")}
                {item.url ? "  ↗" : ""}
              </Text>
            </Pressable>
          ))}
        </View>
      ))}
    </View>
  );
}

function statusColor(status: string, c: (typeof palette)["dark"]) {
  if (status === "done") return { color: c.good };
  if (status === "failed") return { color: c.bad };
  return { color: c.muted };
}

const palette = {
  dark: {
    card: "#14121E",
    border: "#232033",
    text: "#F1EFFA",
    muted: "#8A87A3",
    accent: "#C7B8FF",
    accentText: "#C7B8FF",
    onAccent: "#14101F",
    good: "#8FD6A9",
    bad: "#E58A8A",
  },
  light: {
    card: "#FFFFFF",
    border: "#E8E6E0",
    text: "#25231E",
    muted: "#6E6B65",
    accent: "#4A3A8C",
    accentText: "#4A3A8C",
    onAccent: "#FFFFFF",
    good: "#2E7D4F",
    bad: "#B3453E",
  },
};

const styles = StyleSheet.create({
  card: {
    marginTop: 20,
    marginHorizontal: 16,
    borderRadius: 16,
    borderWidth: 1,
    padding: 16,
  },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  label: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 12, letterSpacing: 2 },
  flightsLink: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 13 },
  sub: { fontSize: 13, lineHeight: 18, marginTop: 6 },
  inputRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 12,
    borderWidth: 1,
    borderRadius: 12,
    paddingLeft: 12,
    paddingRight: 6,
    paddingVertical: 4,
  },
  input: { flex: 1, fontSize: 14, paddingVertical: 8 },
  sendBtn: { borderRadius: 999, paddingHorizontal: 16, paddingVertical: 8 },
  sendText: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 13 },
  hint: { fontSize: 13, marginTop: 12, lineHeight: 18 },
  task: { borderTopWidth: 1, marginTop: 14, paddingTop: 12 },
  taskHeader: { flexDirection: "row", alignItems: "center", gap: 8 },
  status: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 10, letterSpacing: 1 },
  instruction: { fontSize: 13, flex: 1 },
  summary: { fontSize: 13, lineHeight: 18, marginTop: 6 },
  item: { marginTop: 10 },
  itemTitle: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 14 },
  itemMeta: { fontSize: 12, marginTop: 2 },
});
