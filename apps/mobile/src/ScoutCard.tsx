// The scout's home in the app: connect Facebook with one tap (opens the
// streamed login window in Safari) and see what the scout found, tappable.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  StyleSheet,
  Text,
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
}: {
  apiUrl: string;
  auth: Record<string, string>;
  dark: boolean;
}) {
  const [tasks, setTasks] = useState<ScoutTask[]>([]);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<"idle" | "arming">("idle");
  const alive = useRef(true);

  const fetchTasks = useCallback(async (): Promise<ScoutTask[]> => {
    try {
      const res = await fetch(`${apiUrl}/v1/tasks`, { headers: auth });
      if (!res.ok) return [];
      const data = await res.json();
      if (!alive.current) return [];
      setLoginUrl(data.login_url ?? null);
      setTasks(data.tasks ?? []);
      return data.tasks ?? [];
    } catch {
      return [];
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

  const connectFacebook = useCallback(async () => {
    if (connecting !== "idle") return;
    setConnecting("arming");
    try {
      const res = await fetch(`${apiUrl}/v1/tasks`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: "connect facebook", kind: "connect_login" }),
      });
      if (!res.ok) throw new Error("queue failed");
      const { id } = await res.json();
      // The scout picks the task up within ~12s and opens the login page.
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        if (!alive.current) return;
        const list = await fetchTasks();
        const mine = list.find((t) => t.id === id);
        if (mine?.status === "done") {
          if (loginUrl) Linking.openURL(loginUrl);
          setConnecting("idle");
          return;
        }
        if (mine?.status === "failed") throw new Error(mine.error ?? "failed");
      }
      throw new Error("timed out");
    } catch {
      if (alive.current) {
        setConnecting("idle");
        Alert.alert("Couldn't open the login window", "Give it a minute and try again.");
      }
    }
  }, [apiUrl, auth, connecting, fetchTasks, loginUrl]);

  const c = dark ? palette.dark : palette.light;
  const errands = tasks.filter((t) => t.kind !== "connect_login").slice(0, 3);

  return (
    <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
      <View style={styles.headerRow}>
        <Text style={[styles.label, { color: c.muted }]}>SCOUT</Text>
        <Pressable
          style={[styles.connectBtn, { backgroundColor: c.accent }]}
          onPress={connectFacebook}
          disabled={connecting !== "idle"}
        >
          {connecting === "arming" ? (
            <ActivityIndicator size="small" color={c.onAccent} />
          ) : (
            <Text style={[styles.connectText, { color: c.onAccent }]}>
              Log in to Facebook
            </Text>
          )}
        </Pressable>
      </View>
      {connecting === "arming" ? (
        <Text style={[styles.hint, { color: c.muted }]}>
          Opening your login window — Safari will open in ~20 seconds…
        </Text>
      ) : null}

      {errands.length === 0 && connecting === "idle" ? (
        <Text style={[styles.hint, { color: c.muted }]}>
          Ask the orb: “find a used desk under $100 on marketplace”.
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
  connectBtn: {
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    minWidth: 132,
    alignItems: "center",
  },
  connectText: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 13 },
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
