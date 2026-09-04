// The inbox, design-true (Nano V1): Needs you (the email, your draft, the
// consequence, one tap to send or hold), Worth knowing (expandable, with why
// it surfaced), Handled without you (expandable category breakdown), Sent.
// Voice lives in the edge-docked orb — the same realtime conversation as
// everywhere else, interruptible mid-sentence.
import { LinearGradient } from "expo-linear-gradient";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Linking,
  PanResponder,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

const C = {
  bg: "#04040A", panel: "rgba(25,18,51,0.5)", panel2: "#0E0C18",
  border: "rgba(199,184,255,0.14)", text: "#F4F2FA", muted: "#8A87A3",
  body: "#C9C5DA", lav: "#C7B8FF", mint: "#7CF7C4", rose: "#FF9DA8",
  onAccent: "#14101F",
};
const MONO = "Menlo";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";
const TILES: [string, string][] = [
  ["#818CF8", "#4338CA"], ["#5E7CFF", "#25309B"], ["#7C6CFF", "#3B2E8C"],
];

type Draft = { id: string; body: string; status: string; deferred?: boolean };
type Ask = {
  id: string; from_name: string; subject: string; gist: string; why_now: string;
  received_at: string; body: string; draft: Draft | null;
};
type Note = {
  id: string; from_name: string; from_addr: string; subject: string;
  gist: string; why_now: string; kind: string; body: string;
};

// Swipe a row off the list: past the threshold it slides away and settles.
function SwipeRow({ children, onDismiss }: {
  children: React.ReactNode; onDismiss: () => void;
}) {
  const tx = useRef(new Animated.Value(0)).current;
  const pan = useRef(
    PanResponder.create({
      // Grab early on a sideways move and never give the gesture back to
      // the scroll view — that hand-off fight was the rigidity.
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dx) > 6 && Math.abs(g.dx) > Math.abs(g.dy),
      onMoveShouldSetPanResponderCapture: (_e, g) =>
        Math.abs(g.dx) > 10 && Math.abs(g.dx) > Math.abs(g.dy) * 1.4,
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: Animated.event([null, { dx: tx }], { useNativeDriver: false }),
      onPanResponderRelease: (_e, g) => {
        // One committed swipe is enough: distance OR a flick of velocity.
        if (Math.abs(g.dx) > 56 || Math.abs(g.vx) > 0.45) {
          const dir = (g.dx || g.vx) > 0 ? 1 : -1;
          Animated.timing(tx, {
            toValue: dir * 480, duration: 140, useNativeDriver: true,
          }).start(onDismiss);
        } else {
          Animated.spring(tx, {
            toValue: 0, friction: 8, tension: 60, useNativeDriver: true,
          }).start();
        }
      },
      onPanResponderTerminate: () =>
        Animated.spring(tx, { toValue: 0, useNativeDriver: true }).start(),
    })
  ).current;
  const opacity = tx.interpolate({
    inputRange: [-220, 0, 220], outputRange: [0.15, 1, 0.15], extrapolate: "clamp",
  });
  return (
    <Animated.View style={{ transform: [{ translateX: tx }], opacity }} {...pan.panHandlers}>
      {children}
    </Animated.View>
  );
}
type HandledCat = { name: string; n: string; count: number };
type SentItem = { to_name: string; to_addr: string; subject: string; body: string; sent_at: string };
type InboxState = {
  connected: boolean; synced_at: string | null;
  reauth: { needed: boolean; email: string; auth_url: string | null } | null;
  needs_reply: Ask[]; worth_knowing: Note[];
  handled_count: number; handled_categories: HandledCat[]; sent: SentItem[];
};


function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}
function hhmm(iso: string): string {
  return iso.length > 16 ? iso.slice(11, 16) : "";
}

export function InboxScreen({
  apiUrl,
  auth,
  onBack,
  onOpenStage,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onBack: () => void;
  onOpenStage: () => void;
}) {
  const [state, setState] = useState<InboxState | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [openMail, setOpenMail] = useState<string | null>(null);
  const [handledOpen, setHandledOpen] = useState(false);
  const [sentOpen, setSentOpen] = useState<string | null>(null);
  const [busyDraft, setBusyDraft] = useState<string | null>(null);
  const [sentLocal, setSentLocal] = useState<Set<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/v1/inbox/state`, { headers: auth });
      if (res.ok && alive.current) setState(await res.json());
    } catch { /* keep last known */ }
  }, [apiUrl, auth]);

  useEffect(() => {
    alive.current = true;
    refresh();
    const t = setInterval(refresh, 30000);
    return () => { alive.current = false; clearInterval(t); };
  }, [refresh]);

  const draftAction = useCallback(async (draftId: string, action: "send" | "defer") => {
    if (busyDraft) return;
    setBusyDraft(draftId);
    try {
      const res = await fetch(`${apiUrl}/v1/inbox/drafts/${draftId}/${action}`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: action === "defer"
          ? JSON.stringify({ tz_offset_minutes: new Date().getTimezoneOffset() })
          : undefined,
      });
      if ((res.ok || res.status === 409) && action === "send") {
        setSentLocal((s) => new Set(s).add(draftId));
      }
      setTimeout(refresh, 1200);
    } catch { /* next refresh tells the truth */ } finally {
      if (alive.current) setBusyDraft(null);
    }
  }, [apiUrl, auth, busyDraft, refresh]);

  const settleNote = useCallback(async (id: string) => {
    setState((st) => st ? { ...st, worth_knowing: st.worth_knowing.filter((n) => n.id !== id) } : st);
    try {
      await fetch(`${apiUrl}/v1/inbox/notes/${id}/settle`, { method: "POST", headers: auth });
    } catch { /* next refresh reconciles */ }
  }, [apiUrl, auth]);

  const muteRule = useCallback(async (rule: { kind?: string; sender?: string }) => {
    try {
      await fetch(`${apiUrl}/v1/inbox/mute`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify(rule),
      });
      refresh();
    } catch { /* ignore */ }
  }, [apiUrl, auth, refresh]);

  const clearNotes = useCallback(async () => {
    try {
      await fetch(`${apiUrl}/v1/inbox/notes/clear`, { method: "POST", headers: auth });
      refresh();
    } catch { /* ignore */ }
  }, [apiUrl, auth, refresh]);

  if (!state) {
    return <View style={s.center}><ActivityIndicator color={C.lav} /></View>;
  }

  const asks = state.needs_reply;
  const openAsks = asks.filter((a) => !a.draft?.deferred);

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <ScrollView
        contentContainerStyle={s.scroll}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            tintColor={C.lav}
            title="Checking your mail…"
            titleColor={C.muted}
            onRefresh={async () => {
              setRefreshing(true);
              try {
                // Kick the sync think server-side, then pick up what it found.
                await fetch(`${apiUrl}/v1/screen/inbox/refresh`, { method: "POST", headers: auth });
              } catch { /* state refresh below still runs */ }
              await refresh();
              setTimeout(refresh, 6000);
              if (alive.current) setRefreshing(false);
            }}
          />
        }
      >
        <Pressable onPress={onBack} hitSlop={12} style={{ alignSelf: "flex-start" }}>
          <Text style={s.backLink}>‹  MY HUB</Text>
        </Pressable>
        <View style={s.headRow}>
          <View>
            <Text style={s.title}>Inbox Zero</Text>
            <Text style={s.sub}>
              GMAIL{state.synced_at ? `  ·  SYNCED ${hhmm(state.synced_at)}` : ""}
            </Text>
          </View>
          <View style={s.liveChip}><Text style={s.liveText}>LIVE</Text></View>
        </View>

        {state.reauth?.needed ? (
          <View style={s.reauthBanner}>
            <Text style={s.reauthTitle}>Google signed Nano out</Text>
            <Text style={s.reauthBody}>
              Mail stopped syncing{state.reauth.email ? ` for ${state.reauth.email}` : ""}.
              Reconnect and the backlog flows right in.
            </Text>
            {state.reauth.auth_url ? (
              <Pressable style={[s.primaryBtn, { alignSelf: "flex-start", marginTop: 12 }]}
                         onPress={() => Linking.openURL(state.reauth!.auth_url!)}>
                <Text style={s.primaryText}>Reconnect Gmail</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        <View style={s.sectionHead}>
          <Text style={s.sectionTitle}>Needs you</Text>
          <Text style={s.count}>{openAsks.length}</Text>
        </View>
        {asks.length === 0 ? (
          <Text style={s.empty}>Nothing needs your words right now.</Text>
        ) : asks.map((a, i) => {
          const sent = a.draft ? sentLocal.has(a.draft.id) || a.draft.status === "sent" : false;
          const expanded = openMail === a.id;
          return (
            <View key={a.id} style={s.card}>
              <View style={s.cardHead}>
                <LinearGradient colors={TILES[i % TILES.length]}
                                start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.tile}>
                  <Text style={s.tileText}>{initials(a.from_name)}</Text>
                </LinearGradient>
                <View style={{ flex: 1 }}>
                  <Text style={s.from}>{a.from_name}</Text>
                  <Text style={s.subject} numberOfLines={1}>{a.subject}</Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 4 }}>
                  {a.why_now ? (
                    <View style={s.chip}><Text style={s.chipText}>{a.why_now.toUpperCase().slice(0, 18)}</Text></View>
                  ) : null}
                  <Text style={s.at}>{hhmm(a.received_at)}</Text>
                </View>
              </View>
              <Pressable onPress={() => setOpenMail(expanded ? null : a.id)}>
                <Text style={s.quote} numberOfLines={expanded ? undefined : 3}>
                  “{a.body.trim()}”
                </Text>
                {!expanded && a.body.length > 180 ? (
                  <Text style={s.moreHint}>tap to read all</Text>
                ) : null}
              </Pressable>
              {a.draft ? (
                <View style={s.draftBox}>
                  <Text style={s.draftLabel}>YOUR REPLY · WRITTEN</Text>
                  <Text style={s.draftText}>“{a.draft.body}”</Text>
                </View>
              ) : null}
              {a.gist ? <Text style={s.consequence}>{a.gist}</Text> : null}
              {a.draft && !sent ? (
                <View style={s.actions}>
                  <Pressable style={s.primaryBtn} disabled={busyDraft === a.draft.id}
                             onPress={() => draftAction(a.draft!.id, "send")}>
                    <Text style={s.primaryText}>{busyDraft === a.draft.id ? "…" : "Send it"}</Text>
                  </Pressable>
                  <Pressable style={s.ghostBtn} onPress={() => draftAction(a.draft!.id, "defer")}>
                    <Text style={s.ghostText}>{a.draft.deferred ? "Held · asking at 6pm" : "Hold — ask at 6pm"}</Text>
                  </Pressable>
                </View>
              ) : sent ? (
                <Text style={s.sentLabel}>Sent ✓ · it's in the ledger</Text>
              ) : null}
            </View>
          );
        })}

        <View style={s.sectionHead}>
          <Text style={s.sectionTitle}>Worth knowing</Text>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <Text style={s.count}>{state.worth_knowing.length}</Text>
            {state.worth_knowing.length ? (
              <Pressable onPress={clearNotes} hitSlop={8}>
                <Text style={s.clear}>CLEAR</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
        <View style={s.panel}>
          {state.worth_knowing.length === 0 ? (
            <Text style={s.empty}>Nothing worth flagging right now.</Text>
          ) : state.worth_knowing.map((n, i) => {
            const open = openNote === n.id;
            return (
              <SwipeRow key={n.id} onDismiss={() => settleNote(n.id)}>
                <Pressable onPress={() => setOpenNote(open ? null : n.id)}
                           style={[s.noteRow, i > 0 && s.divider]}>
                  <View style={{ flex: 1 }}>
                    <Text style={s.noteFrom}>{n.from_name}</Text>
                    <Text style={s.noteGist}>{n.gist || n.subject}</Text>
                    {open ? (
                      <>
                        <Text style={s.noteBody}>{n.body.trim().slice(0, 1200)}</Text>
                        <Text style={s.noteWhy}>
                          WHY THIS SURFACED{"\n"}
                          <Text style={s.noteWhyBody}>
                            {n.why_now || "Real information, nothing to do — flagged so it doesn't slip past you."}
                          </Text>
                        </Text>
                        <View style={s.muteRow}>
                          {n.kind ? (
                            <Pressable style={s.muteBtn} onPress={() => muteRule({ kind: n.kind })}>
                              <Text style={s.muteText}>Stop showing {n.kind}</Text>
                            </Pressable>
                          ) : null}
                          <Pressable style={s.muteBtnGhost}
                                     onPress={() => muteRule({ sender: n.from_addr })}>
                            <Text style={s.muteTextGhost}>Never from {n.from_name}</Text>
                          </Pressable>
                        </View>
                      </>
                    ) : null}
                  </View>
                  <Text style={s.disclose}>{open ? "–" : "+"}</Text>
                </Pressable>
              </SwipeRow>
            );
          })}
        </View>

        <Pressable style={[s.panel, { marginTop: 24 }]} onPress={() => setHandledOpen(!handledOpen)}>
          <View style={s.handledHead}>
            <Text style={s.handledTitle}>
              <Text style={{ color: C.mint }}>{state.handled_count}</Text> handled without you
            </Text>
            <Text style={s.disclose}>{handledOpen ? "–" : "+"}</Text>
          </View>
          {handledOpen ? (
            <>
              {state.handled_categories.map((c, i) => (
                <View key={c.name} style={[s.noteRow, s.divider]}>
                  <Text style={s.noteFrom}>{c.name}</Text>
                  <Text style={s.catN}>{c.n}</Text>
                </View>
              ))}
              <Text style={s.footer}>None of it was a decision. Undo anything and that kind asks first again.</Text>
            </>
          ) : (
            <Text style={s.footer}>Promotions, newsletters, social pings. Tap to see the breakdown.</Text>
          )}
        </Pressable>

        {state.sent.length ? (
          <>
            <View style={s.sectionHead}>
              <Text style={s.sectionTitle}>Sent</Text>
              <Text style={s.count}>{state.sent.length}</Text>
            </View>
            <View style={s.panel}>
              {state.sent.map((x, i) => {
                const key = `${x.sent_at}-${i}`;
                const open = sentOpen === key;
                return (
                  <Pressable key={key} onPress={() => setSentOpen(open ? null : key)}
                             style={[s.noteRow, i > 0 && s.divider]}>
                    <View style={{ flex: 1 }}>
                      <Text style={s.noteFrom}>To {x.to_name || x.to_addr}</Text>
                      <Text style={s.noteGist} numberOfLines={open ? undefined : 1}>
                        {x.subject || x.body.slice(0, 70)}
                      </Text>
                      {open ? <Text style={s.noteBody}>{x.body}</Text> : null}
                    </View>
                    <Text style={s.at}>{hhmm(x.sent_at)}</Text>
                  </Pressable>
                );
              })}
            </View>
          </>
        ) : null}
      </ScrollView>

      <Pressable style={s.centerBall} onPress={onOpenStage}>
        <LinearGradient colors={["#C7B8FF", "#6D5BD0", "#2A2050"]}
                        start={{ x: 0.2, y: 0.1 }} end={{ x: 0.8, y: 1 }}
                        style={s.centerBallInner} />
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: C.bg },
  scroll: { padding: 20, paddingBottom: 140 },
  backLink: { fontFamily: SANS_SEMI, fontSize: 13, color: C.muted, marginBottom: 14 },
  headRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontFamily: SERIF, fontSize: 38, color: C.text },
  sub: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.muted, marginTop: 4 },
  liveChip: { borderWidth: 1, borderColor: "rgba(124,247,196,0.4)", borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4 },
  liveText: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.mint },
  centerBall: { position: "absolute", bottom: 26, alignSelf: "center" },
  centerBallInner: {
    width: 54, height: 54, borderRadius: 27,
    shadowColor: "#C7B8FF", shadowOpacity: 0.8, shadowRadius: 18,
    shadowOffset: { width: 0, height: 0 },
  },
  muteRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  muteBtn: {
    borderRadius: 999, backgroundColor: "rgba(199,184,255,0.1)",
    borderWidth: 1, borderColor: "rgba(199,184,255,0.3)",
    paddingHorizontal: 12, paddingVertical: 7,
  },
  muteText: { fontFamily: SANS_SEMI, fontSize: 12, color: C.lav },
  muteBtnGhost: {
    borderRadius: 999, borderWidth: 1, borderColor: C.border,
    paddingHorizontal: 12, paddingVertical: 7,
  },
  muteTextGhost: { fontFamily: SANS_SEMI, fontSize: 12, color: C.muted },
  reauthBanner: { borderRadius: 18, borderWidth: 1, borderColor: "rgba(255,157,168,0.45)", backgroundColor: "rgba(255,157,168,0.07)", padding: 16, marginTop: 22 },
  reauthTitle: { fontFamily: SANS_SEMI, fontSize: 15, color: C.rose },
  reauthBody: { fontFamily: SANS, fontSize: 13, lineHeight: 19, color: C.body, marginTop: 6 },
  sectionHead: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between", marginTop: 30, marginBottom: 10 },
  sectionTitle: { fontFamily: SERIF, fontSize: 24, color: C.text },
  count: { fontFamily: SERIF, fontSize: 18, color: C.lav },
  clear: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.muted },
  empty: { fontFamily: SANS, fontSize: 13, color: C.muted, paddingVertical: 8 },
  card: { borderRadius: 20, borderWidth: 1, borderColor: C.border, backgroundColor: C.panel, padding: 16, marginBottom: 12 },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  tile: { width: 42, height: 42, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  tileText: { fontFamily: SANS_SEMI, fontSize: 15, color: "#FFFFFF" },
  from: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  subject: { fontFamily: SANS, fontSize: 12.5, color: C.muted, marginTop: 2 },
  chip: { borderWidth: 1, borderColor: "rgba(255,157,168,0.45)", backgroundColor: "rgba(255,157,168,0.08)", borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3 },
  chipText: { fontFamily: MONO, fontSize: 8.5, letterSpacing: 1, color: C.rose },
  at: { fontFamily: MONO, fontSize: 10, color: C.muted },
  quote: { fontFamily: SANS, fontSize: 14, lineHeight: 20.5, color: C.body, marginTop: 12 },
  moreHint: { fontFamily: MONO, fontSize: 9, letterSpacing: 1, color: C.muted, marginTop: 4 },
  draftBox: { borderRadius: 14, borderWidth: 1, borderColor: "rgba(199,184,255,0.22)", backgroundColor: "rgba(199,184,255,0.06)", padding: 12, marginTop: 12 },
  draftLabel: { fontFamily: MONO, fontSize: 9, letterSpacing: 2, color: C.lav },
  draftText: { fontFamily: SANS, fontSize: 14, lineHeight: 20.5, color: C.text, marginTop: 6 },
  consequence: { fontFamily: SANS, fontSize: 12.5, lineHeight: 18, color: C.muted, marginTop: 10 },
  actions: { flexDirection: "row", gap: 10, marginTop: 14 },
  primaryBtn: { backgroundColor: C.lav, borderRadius: 999, paddingVertical: 11, paddingHorizontal: 20 },
  primaryText: { fontFamily: SANS_SEMI, fontSize: 13.5, color: C.onAccent },
  ghostBtn: { borderWidth: 1, borderColor: C.border, borderRadius: 999, paddingVertical: 11, paddingHorizontal: 16 },
  ghostText: { fontFamily: SANS_SEMI, fontSize: 13, color: C.muted },
  sentLabel: { fontFamily: SANS, fontSize: 13, color: C.mint, marginTop: 12 },
  panel: { borderRadius: 20, borderWidth: 1, borderColor: C.border, backgroundColor: C.panel, paddingHorizontal: 16, paddingVertical: 6 },
  noteRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 12 },
  divider: { borderTopWidth: 1, borderTopColor: "rgba(199,184,255,0.08)" },
  noteFrom: { fontFamily: SANS_SEMI, fontSize: 14, color: C.text },
  noteGist: { fontFamily: SANS, fontSize: 13, lineHeight: 19, color: C.body, marginTop: 3 },
  noteBody: { fontFamily: SANS, fontSize: 13, lineHeight: 19.5, color: C.muted, marginTop: 10 },
  noteWhy: { fontFamily: MONO, fontSize: 9, letterSpacing: 2, color: C.lav, marginTop: 12 },
  noteWhyBody: { fontFamily: SANS, fontSize: 12.5, letterSpacing: 0, lineHeight: 18, color: C.muted },
  disclose: { fontFamily: SANS, fontSize: 18, color: C.muted, marginTop: 2 },
  handledHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 8 },
  handledTitle: { fontFamily: SERIF, fontSize: 20, color: C.text },
  catN: { fontFamily: MONO, fontSize: 11, color: C.mint, marginTop: 3 },
  footer: { fontFamily: SANS, fontSize: 12.5, lineHeight: 18, color: C.muted, paddingBottom: 10 },
});
