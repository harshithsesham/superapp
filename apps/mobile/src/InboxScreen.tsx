// Inbox Zero, matched to Nano V1 (6): a bottom sheet over the dimmed hub —
// drag handle, compact header, Needs you decision cards (THEY WROTE ->
// NANO WROTE BACK), Worth knowing with auto-replied exchanges (THEY ASKED /
// I SENT) and plain notes, and the handled row that expands into
// "where it went". Live data from /v1/inbox/state.
import { LinearGradient } from "expo-linear-gradient";
import React, { useCallback, useEffect, useRef, useState } from "react";
import type { DockContext } from "./NanoOrb";
import {
  ActivityIndicator,
  Alert,
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
  text: "#F4F2FA", muted: "#8A87A3", lav: "#C7B8FF", mint: "#7CF7C4",
  rose: "#FF9DA8",
};
const MONO = "JetBrainsMono_400Regular";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";
const TILES: [string, string][] = [
  ["#818CF8", "#4338CA"], ["#5E7CFF", "#25309B"], ["#7C6CFF", "#3B2E8C"],
];
const CAT_COLORS: Record<string, string> = {
  "Promotions and sales": "#FF9DA8",
  "Newsletters": "#FFD9A0",
  "Social notifications": "#818CF8",
  "Automated notices": "#8A87A3",
  "Receipts": "#7CF7C4",
  "Other noise": "#6B6880",
};

type Draft = { id: string; body: string; status: string; deferred?: boolean };
type Ask = {
  id: string; from_name: string; from_addr?: string; subject: string; gist: string;
  why_now: string; kind: string; received_at: string; body: string; draft: Draft | null;
};
type Note = {
  id: string; from_name: string; from_addr: string; subject: string;
  gist: string; why_now: string; kind: string; body: string; draft?: Draft | null;
};
type HandledCat = { name: string; n: string; count: number };
type InboxState = {
  connected: boolean; synced_at: string | null;
  reauth: { needed: boolean; email: string; auth_url: string | null } | null;
  needs_reply: Ask[]; worth_knowing: Note[];
  handled_count: number; handled_categories: HandledCat[];
  auto_reply_kinds: string[];
};

function SwipeRow({ children, onDismiss }: {
  children: React.ReactNode; onDismiss: () => void;
}) {
  const tx = useRef(new Animated.Value(0)).current;
  const pan = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) =>
        Math.abs(g.dx) > 6 && Math.abs(g.dx) > Math.abs(g.dy),
      onMoveShouldSetPanResponderCapture: (_e, g) =>
        Math.abs(g.dx) > 10 && Math.abs(g.dx) > Math.abs(g.dy) * 1.4,
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: Animated.event([null, { dx: tx }], { useNativeDriver: false }),
      onPanResponderRelease: (_e, g) => {
        if (Math.abs(g.dx) > 56 || Math.abs(g.vx) > 0.45) {
          const dir = (g.dx || g.vx) > 0 ? 1 : -1;
          Animated.timing(tx, {
            toValue: dir * 480, duration: 140, useNativeDriver: true,
          }).start(onDismiss);
        } else {
          Animated.spring(tx, { toValue: 0, friction: 8, tension: 60, useNativeDriver: true }).start();
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

function initials(name: string): string {
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] ?? "?") + (p[1]?.[0] ?? "")).toUpperCase();
}
function hhmm(iso: string | null): string {
  return iso && iso.length > 16 ? iso.slice(11, 16) : "";
}

export function InboxScreen({
  apiUrl,
  auth,
  onBack,
  onLongPressItem,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onBack: () => void;
  onLongPressItem?: (ctx: DockContext) => void;
}) {
  const [state, setState] = useState<InboxState | null>(null);
  const [openMail, setOpenMail] = useState<string | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [handledOpen, setHandledOpen] = useState(false);
  const [busyDraft, setBusyDraft] = useState<string | null>(null);
  const [sentLocal, setSentLocal] = useState<Set<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const resolvedIds = useRef<Set<string>>(new Set());

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

  const draftAction = useCallback(async (draftId: string, action: "send" | "defer" | "dismiss") => {
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
        setSentLocal((s0) => new Set(s0).add(draftId));
      }
      setTimeout(refresh, 1200);
    } catch { /* refresh reconciles */ } finally {
      if (alive.current) setBusyDraft(null);
    }
  }, [apiUrl, auth, busyDraft, refresh]);

  const settleNote = useCallback(async (id: string) => {
    setState((st) => st ? { ...st, worth_knowing: st.worth_knowing.filter((n) => n.id !== id) } : st);
    try {
      await fetch(`${apiUrl}/v1/inbox/notes/${id}/settle`, { method: "POST", headers: auth });
    } catch { /* refresh reconciles */ }
  }, [apiUrl, auth]);

  // Local-only removal after Nano resolves a card from the dock (the server
  // mute/dismiss already ran there). The card leaves the list immediately.
  const dropNote = useCallback((id: string) => {
    resolvedIds.current.add(id);
    setState((st) => st ? { ...st, worth_knowing: st.worth_knowing.filter((n) => n.id !== id) } : st);
    setTimeout(refresh, 1500);
  }, [refresh]);
  const dropAsk = useCallback((id: string) => {
    resolvedIds.current.add(id);
    setState((st) => st ? { ...st, needs_reply: st.needs_reply.filter((a) => a.id !== id) } : st);
    setTimeout(refresh, 1500);
  }, [refresh]);

  const toggleAutoReply = useCallback(async (kind: string, active: boolean) => {
    try {
      await fetch(`${apiUrl}/v1/inbox/autoreply`, {
        method: active ? "DELETE" : "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ kind }),
      });
      refresh();
    } catch { /* ignore */ }
  }, [apiUrl, auth, refresh]);

  const autoReplySettings = useCallback(() => {
    const kinds = state?.auto_reply_kinds ?? [];
    if (!kinds.length) {
      Alert.alert("Auto-reply", "No streams are on auto-reply yet. Turn one on from a draft card.");
      return;
    }
    Alert.alert("Auto-replying to", kinds.join("\n"), [
      ...kinds.map((k) => ({ text: `Stop: ${k}`, onPress: () => toggleAutoReply(k, true) })),
      { text: "Done", style: "cancel" as const },
    ]);
  }, [state, toggleAutoReply]);

  const clearNotes = useCallback(async () => {
    try {
      await fetch(`${apiUrl}/v1/inbox/notes/clear`, { method: "POST", headers: auth });
      refresh();
    } catch { /* ignore */ }
  }, [apiUrl, auth, refresh]);

  const asks = (state?.needs_reply ?? []).filter((a) => !resolvedIds.current.has(a.id));
  const openAsks = asks.filter((a) => !a.draft?.deferred);
  const notes = (state?.worth_knowing ?? []).filter((n) => !resolvedIds.current.has(n.id));
  const autoNotes = notes.filter((n) => n.draft?.status === "sent");
  const plainNotes = notes.filter((n) => n.draft?.status !== "sent");

  return (
    <View style={s.backdrop}>
      <LinearGradient colors={["#141232", "#0B0910"]} locations={[0, 0.58]} style={s.sheet}>
        <Pressable onPress={onBack} hitSlop={12} style={s.backRow}>
          <Text style={s.backText}>‹  MY HUB</Text>
        </Pressable>
        <View style={s.header}>
          <LinearGradient colors={["#818CF8", "#4338CA"]}
                          start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.headTile}>
            <Text style={s.headTileText}>I</Text>
          </LinearGradient>
          <View style={{ flex: 1 }}>
            <Text style={s.headName}>Inbox Zero</Text>
            <Text style={s.headSub}>
              Gmail{state?.synced_at ? ` · synced ${hhmm(state.synced_at)}` : ""}
            </Text>
          </View>
        </View>

        {!state ? (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
            <ActivityIndicator color={C.lav} />
          </View>
        ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={s.scroll}
          refreshControl={
            <RefreshControl
              refreshing={refreshing} tintColor={C.lav}
              onRefresh={async () => {
                setRefreshing(true);
                try {
                  await fetch(`${apiUrl}/v1/screen/inbox/refresh`, { method: "POST", headers: auth });
                } catch { /* state refresh below still runs */ }
                await refresh();
                setTimeout(refresh, 6000);
                if (alive.current) setRefreshing(false);
              }}
            />
          }
        >
          {state.reauth?.needed ? (
            <View style={s.reauth}>
              <Text style={s.reauthTitle}>Google signed Nano out</Text>
              <Text style={s.reauthBody}>Mail stopped syncing. Reconnect and the backlog flows in.</Text>
              {state.reauth.auth_url ? (
                <Pressable style={s.reauthBtn} onPress={() => Linking.openURL(state.reauth!.auth_url!)}>
                  <Text style={s.reauthBtnText}>Reconnect Gmail</Text>
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
          ) : (
            <View style={{ gap: 10 }}>
              {asks.map((a, i) => {
                const sent = a.draft ? sentLocal.has(a.draft.id) || a.draft.status === "sent" : false;
                const expanded = openMail === a.id || asks.length === 1;
                const autoOn = a.kind
                  ? state.auto_reply_kinds.some((k) => k.toLowerCase() === a.kind.toLowerCase())
                  : false;
                return (
                  <View key={a.id} style={[s.card, a.why_now ? s.cardUrgent : null]}>
                    <Pressable style={s.cardHead} onPress={() => setOpenMail(expanded ? null : a.id)}
                               onLongPress={() => onLongPressItem?.({
                                 type: "decision", label: a.from_name, kind: a.kind || undefined,
                                 fromAddr: a.from_addr, draftId: a.draft?.id,
                                 onResolved: () => dropAsk(a.id),
                               })} delayLongPress={350}>
                      <LinearGradient colors={TILES[i % TILES.length]}
                                      start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.tile}>
                        <Text style={s.tileText}>{initials(a.from_name)}</Text>
                      </LinearGradient>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Text style={s.from}>{a.from_name}</Text>
                        <Text style={s.subject} numberOfLines={1}>{a.subject}</Text>
                      </View>
                      {a.why_now ? (
                        <View style={s.chip}><Text style={s.chipText}>{a.why_now.toUpperCase().slice(0, 14)}</Text></View>
                      ) : null}
                    </Pressable>
                    {expanded ? (
                      <View style={{ paddingHorizontal: 15, paddingBottom: 15 }}>
                        <View style={s.theyWrote}>
                          <View style={{ flexDirection: "row", alignItems: "baseline" }}>
                            <Text style={[s.panelLabel, { flex: 1 }]}>THEY WROTE</Text>
                            <Text style={s.panelAt}>{hhmm(a.received_at)}</Text>
                          </View>
                          <Text style={s.panelBody} numberOfLines={openMail === a.id ? undefined : 6}>
                            {a.body.trim()}
                          </Text>
                        </View>
                        <View style={s.connector}>
                          <View style={s.connLine} />
                          <Text style={{ color: "rgba(199,184,255,0.6)", fontSize: 11 }}>↓</Text>
                        </View>
                        {a.draft ? (
                          <View style={s.nanoWrote}>
                            <Text style={[s.panelLabel, { color: "rgba(199,184,255,0.9)" }]}>NANO WROTE BACK</Text>
                            <Text style={[s.panelBody, { color: "rgba(244,242,250,0.84)" }]}>{a.draft.body}</Text>
                          </View>
                        ) : null}
                        {a.gist ? <Text style={s.consequence}>{a.gist}</Text> : null}
                        {a.draft && !sent ? (
                          <>
                            <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
                              <Pressable style={s.sendBtn} disabled={busyDraft === a.draft.id}
                                         onPress={() => draftAction(a.draft!.id, "send")}>
                                <Text style={s.sendText}>{busyDraft === a.draft.id ? "…" : "Send it"}</Text>
                              </Pressable>
                              <Pressable style={s.ghostBtn} onPress={() => draftAction(a.draft!.id, "dismiss")}>
                                <Text style={s.ghostText}>Dismiss</Text>
                              </Pressable>
                              <Pressable style={[s.ghostBtn, { borderColor: "rgba(255,255,255,0.12)" }]}
                                         onPress={() => draftAction(a.draft!.id, "defer")}>
                                <Text style={[s.ghostText, { color: "rgba(244,242,250,0.6)" }]}>
                                  {a.draft.deferred ? "Held" : "6pm"}
                                </Text>
                              </Pressable>
                            </View>
                            {a.kind ? (
                              <Pressable style={s.autoRow} onPress={() => toggleAutoReply(a.kind, autoOn)}>
                                <Text style={{ color: "rgba(199,184,255,0.85)", fontSize: 12 }}>{"«"}</Text>
                                <Text style={s.autoText}>
                                  {autoOn ? `Stop auto-replying to ${a.kind}` : `Auto-reply to ${a.kind} from now on`}
                                </Text>
                              </Pressable>
                            ) : null}
                          </>
                        ) : sent ? (
                          <Text style={s.sentLabel}>Sent ✓ · it's in the ledger</Text>
                        ) : null}
                      </View>
                    ) : null}
                  </View>
                );
              })}
            </View>
          )}

          <View style={[s.sectionHead, { marginTop: 26 }]}>
            <Text style={s.sectionTitle}>Worth knowing</Text>
            {state.worth_knowing.length ? (
              <Pressable onPress={clearNotes} hitSlop={8}>
                <Text style={s.clear}>Clear</Text>
              </Pressable>
            ) : <Text style={s.count}>0</Text>}
          </View>
          {state.worth_knowing.length ? (
            <Text style={s.holdHint}>PRESS AND HOLD ANY CARD TO TELL ME WHAT TO DO WITH IT</Text>
          ) : null}
          <View style={{ gap: 8 }}>
            {autoNotes.map((n) => {
              const open = openNote === n.id;
              return (
                <SwipeRow key={n.id} onDismiss={() => settleNote(n.id)}>
                  <View style={s.autoNote}>
                    <Pressable style={s.autoNoteHead}
                               onPress={() => setOpenNote(open ? null : n.id)}
                               onLongPress={() => onLongPressItem?.({ type: "note", label: n.from_name, kind: n.kind || undefined, fromAddr: n.from_addr, noteId: n.id, why: n.gist, onResolved: () => dropNote(n.id) })} delayLongPress={350}>
                      <Text style={{ color: "rgba(124,247,196,0.8)", fontSize: 12, marginTop: 2 }}>{"«"}</Text>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                          <Text style={[s.noteFrom, { flex: 1 }]}>{n.from_name}</Text>
                          <Text style={s.autoStamp}>AUTO-REPLIED</Text>
                        </View>
                        <Text style={s.microLabel}>THEY ASKED</Text>
                        <Text style={s.microBody} numberOfLines={2}>{n.gist || n.subject}</Text>
                        <Text style={[s.microLabel, { color: "rgba(124,247,196,0.7)", marginTop: 9 }]}>I SENT</Text>
                        <Text style={[s.microBody, { color: "rgba(244,242,250,0.82)" }]} numberOfLines={open ? undefined : 2}>
                          {n.draft?.body ?? ""}
                        </Text>
                      </View>
                    </Pressable>
                    {open ? (
                      <View style={{ paddingHorizontal: 14, paddingBottom: 14 }}>
                        <View style={s.hairline} />
                        <Text style={s.microLabel}>WHAT WENT OUT</Text>
                        <Text style={s.wentOut}>{n.draft?.body ?? ""}</Text>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 7, marginTop: 10 }}>
                          <View style={s.nanoDot} />
                          <Text style={s.byNano}>WRITTEN BY NANO</Text>
                        </View>
                        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 7, marginTop: 13 }}>
                          {n.kind ? (
                            <Pressable style={s.stopPill} onPress={() => toggleAutoReply(n.kind, true)}>
                              <Text style={s.stopPillText}>Stop auto-replying to {n.kind}</Text>
                            </Pressable>
                          ) : null}
                          <Pressable style={s.settingsPill} onPress={autoReplySettings}>
                            <Text style={s.settingsPillText}>Auto-reply settings</Text>
                          </Pressable>
                        </View>
                      </View>
                    ) : null}
                  </View>
                </SwipeRow>
              );
            })}
            {plainNotes.map((n) => {
              const open = openNote === n.id;
              return (
                <SwipeRow key={n.id} onDismiss={() => settleNote(n.id)}>
                  <Pressable style={s.note}
                             onPress={() => setOpenNote(open ? null : n.id)}
                             onLongPress={() => onLongPressItem?.({ type: "note", label: n.from_name, kind: n.kind || undefined, fromAddr: n.from_addr, noteId: n.id, why: n.gist, onResolved: () => dropNote(n.id) })} delayLongPress={350}>
                    <View style={s.noteDot} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={s.noteFrom}>{n.from_name}</Text>
                      <Text style={s.noteGist} numberOfLines={open ? undefined : 2}>{n.gist || n.subject}</Text>
                      {open ? (
                        <>
                          <Text style={s.noteBody}>{n.body.trim().slice(0, 1200)}</Text>
                          <Text style={[s.microLabel, { marginTop: 12 }]}>WHY THIS SURFACED</Text>
                          <Text style={s.microBody}>
                            {n.why_now || "Real information, nothing to do — flagged so it doesn't slip past you."}
                          </Text>
                        </>
                      ) : null}
                    </View>
                  </Pressable>
                </SwipeRow>
              );
            })}
            {!state.worth_knowing.length ? (
              <Text style={s.empty}>Nothing worth flagging right now.</Text>
            ) : null}
          </View>

          <Pressable style={s.handled} onPress={() => setHandledOpen(!handledOpen)}>
            <Text style={{ color: C.mint, fontSize: 14 }}>✓</Text>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.handledTitle}>{state.handled_count} handled without you</Text>
              <Text style={s.handledSub}>Promotions, newsletters, receipts. None of it was a decision.</Text>
            </View>
            <Text style={s.handledToggle}>{handledOpen ? "HIDE" : "SHOW"}</Text>
          </Pressable>
          {handledOpen ? (
            <View style={{ gap: 7, marginTop: 9 }}>
              <View style={s.whereRow}>
                <Text style={s.whereLabel}>WHERE IT WENT</Text>
                <View style={s.whereLine} />
                <Text style={[s.whereLabel, { color: "rgba(244,242,250,0.35)" }]}>GMAIL</Text>
              </View>
              {state.handled_categories.map((c) => (
                <View key={c.name} style={s.catRow}>
                  <View style={[s.catChip, { backgroundColor: CAT_COLORS[c.name] ?? "#6B6880" }]} />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={s.catName}>{c.name}</Text>
                    <Text style={s.catSub}>{c.n}</Text>
                  </View>
                </View>
              ))}
            </View>
          ) : null}
        </ScrollView>
        )}
      </LinearGradient>
    </View>
  );
}

const s = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "#0B0910" },
  sheet: { ...StyleSheet.absoluteFillObject },
  backRow: { paddingHorizontal: 20, paddingTop: 62, paddingBottom: 14, alignSelf: "flex-start" },
  backText: { fontFamily: SANS_SEMI, fontSize: 13, color: "rgba(244,242,250,0.6)" },
  header: { flexDirection: "row", alignItems: "center", gap: 11, paddingHorizontal: 20 },
  headTile: { width: 38, height: 38, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  headTileText: { fontFamily: SANS_SEMI, fontSize: 15, color: "#FFFFFF" },
  headName: { fontFamily: SANS_SEMI, fontSize: 16, color: C.text },
  headSub: { fontFamily: SANS, fontSize: 11.5, color: "rgba(244,242,250,0.5)", marginTop: 2 },
  scroll: { paddingHorizontal: 20, paddingTop: 18, paddingBottom: 140 },
  reauth: {
    borderRadius: 18, borderWidth: 1, borderColor: "rgba(255,157,168,0.45)",
    backgroundColor: "rgba(255,157,168,0.07)", padding: 15, marginBottom: 20,
  },
  reauthTitle: { fontFamily: SANS_SEMI, fontSize: 14, color: C.rose },
  reauthBody: { fontFamily: SANS, fontSize: 12.5, lineHeight: 18, color: "rgba(244,242,250,0.7)", marginTop: 5 },
  reauthBtn: {
    alignSelf: "flex-start", marginTop: 11, backgroundColor: C.lav,
    borderRadius: 100, paddingHorizontal: 16, paddingVertical: 10,
  },
  reauthBtnText: { fontFamily: SANS_SEMI, fontSize: 12.5, color: "#14101F" },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 11 },
  sectionTitle: { fontFamily: SANS_SEMI, fontSize: 16, color: C.text },
  count: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 1.5, color: "rgba(244,242,250,0.62)" },
  clear: { fontFamily: SANS, fontSize: 12, color: C.lav },
  holdHint: {
    fontFamily: MONO, fontSize: 9.5, letterSpacing: 1.2,
    color: "rgba(244,242,250,0.38)", marginTop: -4, marginBottom: 11,
  },
  empty: { fontFamily: SANS, fontSize: 13, color: C.muted, paddingVertical: 6 },
  card: {
    borderRadius: 22, overflow: "hidden",
    backgroundColor: "rgba(255,255,255,0.05)",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  cardUrgent: { borderColor: "rgba(255,157,168,0.35)" },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 11, paddingHorizontal: 15, paddingVertical: 14 },
  tile: { width: 32, height: 32, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  tileText: { fontFamily: SANS_SEMI, fontSize: 12, color: "#FFFFFF" },
  from: { fontFamily: SANS_SEMI, fontSize: 13.5, color: C.text },
  subject: { fontFamily: SANS, fontSize: 11.5, color: "rgba(244,242,250,0.55)", marginTop: 2 },
  chip: {
    borderWidth: 1, borderColor: "rgba(255,157,168,0.4)",
    backgroundColor: "rgba(255,157,168,0.1)", borderRadius: 100,
    paddingHorizontal: 10, paddingVertical: 5,
  },
  chipText: { fontFamily: MONO, fontSize: 10, letterSpacing: 0.8, color: C.rose },
  theyWrote: {
    padding: 13, borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.035)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  nanoWrote: {
    padding: 13, borderRadius: 16,
    backgroundColor: "rgba(199,184,255,0.09)", borderWidth: 1, borderColor: "rgba(199,184,255,0.22)",
  },
  panelLabel: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 1.8, color: "rgba(244,242,250,0.6)" },
  panelAt: { fontFamily: MONO, fontSize: 10.5, color: "rgba(244,242,250,0.5)" },
  panelBody: { fontFamily: SANS, fontSize: 12.5, lineHeight: 19.5, color: "rgba(244,242,250,0.72)", marginTop: 8 },
  connector: { flexDirection: "row", alignItems: "center", gap: 9, marginVertical: 10, marginLeft: 14 },
  connLine: { width: 1, height: 14, backgroundColor: "rgba(199,184,255,0.4)" },
  consequence: { fontFamily: SANS, fontSize: 11.5, lineHeight: 16.5, color: "rgba(244,242,250,0.5)", marginTop: 10 },
  sendBtn: { flex: 1, alignItems: "center", padding: 12, borderRadius: 100, backgroundColor: C.lav },
  sendText: { fontFamily: SANS_SEMI, fontSize: 12.5, color: "#14101F" },
  ghostBtn: {
    paddingHorizontal: 15, paddingVertical: 12, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.06)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
  },
  ghostText: { fontFamily: SANS_SEMI, fontSize: 12.5, color: "rgba(244,242,250,0.8)" },
  autoRow: {
    flexDirection: "row", alignItems: "center", gap: 8, marginTop: 12,
    paddingTop: 11, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.07)",
  },
  autoText: { flex: 1, fontFamily: SANS, fontSize: 11.5, lineHeight: 16, color: "rgba(199,184,255,0.85)" },
  sentLabel: { fontFamily: SANS_SEMI, fontSize: 11.5, color: C.mint, paddingBottom: 2, paddingTop: 10 },
  autoNote: {
    borderRadius: 18, backgroundColor: "rgba(255,255,255,0.04)",
    borderWidth: 1, borderColor: "rgba(124,247,196,0.18)",
  },
  autoNoteHead: { flexDirection: "row", alignItems: "flex-start", gap: 11, paddingHorizontal: 14, paddingVertical: 13 },
  autoStamp: { fontFamily: MONO, fontSize: 9.5, letterSpacing: 0.8, color: "rgba(124,247,196,0.85)" },
  microLabel: { fontFamily: MONO, fontSize: 9.5, letterSpacing: 1.6, color: "rgba(244,242,250,0.42)", marginTop: 8 },
  microBody: { fontFamily: SANS, fontSize: 12, lineHeight: 17.5, color: "rgba(244,242,250,0.6)", marginTop: 3 },
  hairline: { height: 1, backgroundColor: "rgba(255,255,255,0.08)", marginBottom: 12 },
  wentOut: {
    fontFamily: SERIF, fontStyle: "italic", fontSize: 14.5, lineHeight: 21.5,
    color: "rgba(244,242,250,0.82)", marginTop: 7,
  },
  nanoDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: "rgba(199,184,255,0.6)" },
  byNano: { fontFamily: MONO, fontSize: 10, letterSpacing: 1.8, color: "rgba(199,184,255,0.8)" },
  stopPill: {
    paddingHorizontal: 13, paddingVertical: 9, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.06)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
  },
  stopPillText: { fontFamily: SANS_SEMI, fontSize: 11.5, color: "rgba(244,242,250,0.82)" },
  settingsPill: {
    paddingHorizontal: 13, paddingVertical: 9, borderRadius: 100,
    borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
  },
  settingsPillText: { fontFamily: SANS_SEMI, fontSize: 11.5, color: "rgba(244,242,250,0.6)" },
  note: {
    flexDirection: "row", alignItems: "flex-start", gap: 11,
    paddingHorizontal: 14, paddingVertical: 13, borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.04)", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  noteDot: { width: 7, height: 7, borderRadius: 4, marginTop: 5, backgroundColor: C.lav },
  noteFrom: { fontFamily: SANS_SEMI, fontSize: 12.5, color: C.text },
  noteGist: { fontFamily: SANS, fontSize: 12, lineHeight: 17.5, color: "rgba(244,242,250,0.6)", marginTop: 3 },
  noteBody: { fontFamily: SANS, fontSize: 12, lineHeight: 18, color: "rgba(244,242,250,0.55)", marginTop: 10 },
  handled: {
    flexDirection: "row", alignItems: "center", gap: 11, marginTop: 22,
    paddingHorizontal: 15, paddingVertical: 14, borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.035)", borderWidth: 1, borderColor: "rgba(255,255,255,0.07)",
  },
  handledTitle: { fontFamily: SANS, fontSize: 13, color: C.text },
  handledSub: { fontFamily: SANS, fontSize: 11, color: "rgba(244,242,250,0.5)", marginTop: 2 },
  handledToggle: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 1, color: "rgba(244,242,250,0.62)" },
  whereRow: { flexDirection: "row", alignItems: "center", gap: 9, marginHorizontal: 2 },
  whereLabel: { fontFamily: MONO, fontSize: 10, letterSpacing: 1.6, color: "rgba(244,242,250,0.42)" },
  whereLine: { flex: 1, height: 1, backgroundColor: "rgba(255,255,255,0.07)" },
  catRow: {
    flexDirection: "row", alignItems: "center", gap: 11,
    paddingHorizontal: 14, paddingVertical: 11, borderRadius: 14,
    backgroundColor: "rgba(255,255,255,0.025)", borderWidth: 1, borderColor: "rgba(255,255,255,0.06)",
  },
  catChip: { width: 9, height: 9, borderRadius: 3 },
  catName: { fontFamily: SANS_SEMI, fontSize: 12.5, color: C.text },
  catSub: { fontFamily: SANS, fontSize: 10.5, color: "rgba(244,242,250,0.5)", marginTop: 2 },
});
