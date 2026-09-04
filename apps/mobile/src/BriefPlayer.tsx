// The morning briefing, played: a full-screen story-style walkthrough of the
// inbox — segment by segment, spoken aloud with a live caption — built to
// match Nano V1 (6) exactly: progress bars, agent chip, voice toggle, the
// pulsing orb with a typing caption, and the show/next pill pair.
import { LinearGradient } from "expo-linear-gradient";
import { useAudioPlayer } from "expo-audio";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator, Animated, Easing, Pressable, ScrollView, StyleSheet,
  Text, View,
} from "react-native";

const T = "#F4F2FA";
const MONO = "JetBrainsMono_400Regular";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";

type Stat = { n: string; label: string; color: string };
type MailRow = { initials: string; from: string; sub: string; chip: string };
type NoteRow = { from: string; gist: string };
type LedgerRow = { text: string; n: string };
type Segment = {
  key: string; agent: string; mono: string; stamp: string;
  head: string; say: string; showLabel: string; nextLabel: string;
  stats?: Stat[]; mailRows?: MailRow[]; noteRows?: NoteRow[]; ledger?: LedgerRow[];
  fieldColor: string;
};

function initials(name: string): string {
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] ?? "?") + (p[1]?.[0] ?? "")).toUpperCase();
}
const spell = (n: number) =>
  ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"][n] ?? String(n);

export function BriefPlayer({
  apiUrl, auth, onClose, onOpenInbox,
}: {
  apiUrl: string; auth: Record<string, string>;
  onClose: () => void; onOpenInbox: () => void;
}) {
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [voiceOn, setVoiceOn] = useState(true);
  const [capN, setCapN] = useState(0);
  const [sayUrl, setSayUrl] = useState<string | null>(null);
  const capTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pulse = useRef(new Animated.Value(1)).current;
  const player = useAudioPlayer(sayUrl ? { uri: sayUrl, headers: auth } : null);

  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1.1, duration: 2000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 2000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/v1/inbox/state`, { headers: auth });
        const d = await res.json();
        const asks = (d.needs_reply ?? []).filter((a: any) => !a.draft?.deferred);
        const notes = d.worth_knowing ?? [];
        const cats: any[] = d.handled_categories ?? [];
        const handled = d.handled_count ?? 0;
        const total = handled + asks.length + notes.length;
        const segs: Segment[] = [{
          key: "mail", agent: "Inbox Zero", mono: "I",
          stamp: `${total} in your primary`,
          head: asks.length
            ? `${spell(asks.length)} repl${asks.length === 1 ? "y needs" : "ies need"} your yes.`
            : "Nothing needs your words.",
          say: `${total} emails in your primary. I handled ${handled} and ` +
            (asks.length
              ? `wrote the ${asks.length === 1 ? "reply" : "replies"} that need you.`
              : "nothing is waiting on your words."),
          showLabel: "Open the drafts", nextLabel: "Next",
          fieldColor: "rgba(129,140,248,0.20)",
          stats: [
            { n: String(handled), label: "handled without you", color: "#7CF7C4" },
            { n: String(asks.length), label: "need a reply", color: "#C7B8FF" },
            { n: String(notes.length), label: "to read", color: T },
          ],
          mailRows: asks.slice(0, 2).map((a: any) => ({
            initials: initials(a.from_name), from: a.from_name,
            sub: a.subject || a.gist, chip: (a.why_now || "DRAFTED").toUpperCase().slice(0, 16),
          })),
        }, {
          key: "notes", agent: "Inbox Zero", mono: "I", stamp: "Worth reading",
          head: notes.length
            ? `${spell(notes.length)} thing${notes.length === 1 ? "" : "s"},\nno action.`
            : "Nothing worth\nflagging.",
          say: notes.length
            ? `${spell(notes.length)} worth knowing and none of them need a reply.`
            : "Nothing worth flagging today.",
          showLabel: "Open the inbox", nextLabel: "Next",
          fieldColor: "rgba(129,140,248,0.14)",
          noteRows: notes.slice(0, 3).map((n: any) => ({
            from: n.from_name, gist: n.gist || n.subject,
          })),
        }, {
          key: "done", agent: "Nano", mono: "N", stamp: "That is everything",
          head: "That is your inbox.",
          say: "Everything below happened without asking you. I will come to you if anything changes.",
          showLabel: "Open the inbox", nextLabel: "Back to the Hub",
          fieldColor: "rgba(199,184,255,0.18)",
          ledger: cats.map((c: any) => ({ text: c.name, n: c.n.split(" ")[0] })),
        }];
        setSegments(segs);
      } catch {
        onClose();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const seg = segments?.[idx];

  // Caption types itself; the same line is spoken when voice is on.
  useEffect(() => {
    if (!seg) return;
    setCapN(0);
    if (capTimer.current) clearInterval(capTimer.current);
    capTimer.current = setInterval(() => {
      setCapN((n) => {
        if (n + 1 >= seg.say.length) {
          if (capTimer.current) clearInterval(capTimer.current);
          return seg.say.length;
        }
        return n + 2;
      });
    }, 42);
    if (voiceOn) {
      setSayUrl(`${apiUrl}/v1/voice/speak?text=${encodeURIComponent(seg.say)}`);
    }
    return () => {
      if (capTimer.current) clearInterval(capTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, segments]);

  useEffect(() => {
    if (sayUrl && voiceOn) {
      try { player.play(); } catch { /* muted device */ }
    }
    if (!voiceOn) {
      try { player.pause(); } catch { /* fine */ }
    }
  }, [sayUrl, voiceOn, player]);

  const next = useCallback(() => {
    if (!segments) return;
    if (idx + 1 >= segments.length) onClose();
    else setIdx(idx + 1);
  }, [idx, segments, onClose]);

  if (!seg) {
    return (
      <View style={[s.root, { alignItems: "center", justifyContent: "center" }]}>
        <ActivityIndicator color="#C7B8FF" />
      </View>
    );
  }

  return (
    <View style={s.root}>
      <View style={[s.field, { backgroundColor: seg.fieldColor }]} pointerEvents="none" />

      <View style={s.top}>
        <View style={{ flexDirection: "row", gap: 4 }}>
          {segments!.map((_, i) => (
            <View key={i} style={s.barTrack}>
              <View style={[s.barFill, { width: i < idx ? "100%" : i === idx ? "55%" : "0%" }]} />
            </View>
          ))}
        </View>
        <View style={s.agentRow}>
          <LinearGradient
            colors={seg.key === "done" ? ["#C7B8FF", "#6D28D9"] : ["#818CF8", "#4338CA"]}
            start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.agentTile}>
            <Text style={s.agentTileText}>{seg.mono}</Text>
          </LinearGradient>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.agentName}>{seg.agent}</Text>
            <Text style={s.agentStamp}>{seg.stamp.toUpperCase()}</Text>
          </View>
          <Pressable style={[s.mutePill, !voiceOn && s.mutePillOff]}
                     onPress={() => setVoiceOn(!voiceOn)} hitSlop={8}>
            <Text style={[s.muteText, !voiceOn && { color: "rgba(244,242,250,0.55)" }]}>
              {voiceOn ? "VOICE" : "MUTED"}
            </Text>
          </Pressable>
          <Pressable onPress={onClose} hitSlop={12} style={{ padding: 6 }}>
            <Text style={{ color: "rgba(255,255,255,0.7)", fontSize: 15 }}>✕</Text>
          </Pressable>
        </View>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={s.body}>
        <Text style={s.head}>{seg.head}</Text>

        {seg.stats ? (
          <View style={{ flexDirection: "row", gap: 9, marginTop: 20 }}>
            {seg.stats.map((st) => (
              <View key={st.label} style={s.statTile}>
                <Text style={[s.statN, { color: st.color }]}>{st.n}</Text>
                <Text style={s.statLabel}>{st.label}</Text>
              </View>
            ))}
          </View>
        ) : null}

        {seg.mailRows?.length ? (
          <View style={{ gap: 8, marginTop: 11 }}>
            {seg.mailRows.map((r) => (
              <View key={r.from} style={s.mailRow}>
                <LinearGradient colors={["#818CF8", "#4338CA"]}
                                start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.mailAvatar}>
                  <Text style={s.mailAvatarText}>{r.initials}</Text>
                </LinearGradient>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.mailFrom}>{r.from}</Text>
                  <Text style={s.mailSub} numberOfLines={1}>{r.sub}</Text>
                </View>
                <View style={s.mailChip}><Text style={s.mailChipText}>{r.chip}</Text></View>
              </View>
            ))}
          </View>
        ) : null}

        {seg.noteRows ? (
          <View style={{ gap: 8, marginTop: 20 }}>
            {seg.noteRows.map((n) => (
              <View key={n.from + n.gist.slice(0, 8)} style={s.noteRow}>
                <View style={s.noteDot} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.noteFrom}>{n.from}</Text>
                  <Text style={s.noteGist}>{n.gist}</Text>
                </View>
              </View>
            ))}
          </View>
        ) : null}

        {seg.ledger ? (
          <View style={{ gap: 8, marginTop: 20 }}>
            {seg.ledger.map((l) => (
              <View key={l.text} style={s.ledgerRow}>
                <LinearGradient colors={["#818CF8", "#4338CA"]}
                                start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.ledgerTile}>
                  <Text style={s.ledgerTileText}>I</Text>
                </LinearGradient>
                <Text style={s.ledgerText}>{l.text}</Text>
                <Text style={s.ledgerN}>{l.n}</Text>
              </View>
            ))}
          </View>
        ) : null}
      </ScrollView>

      <View style={s.bottom}>
        <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12 }}>
          <View style={{ width: 56, height: 56, alignItems: "center", justifyContent: "center" }}>
            <Animated.View style={[s.orb, { transform: [{ scale: pulse }] }]}>
              <LinearGradient colors={["#D6CCFF", "#5B49C9", "#0E0A1E"]}
                              start={{ x: 0.34, y: 0.28 }} end={{ x: 0.9, y: 1 }}
                              style={{ flex: 1, borderRadius: 22 }} />
            </Animated.View>
          </View>
          <View style={{ flex: 1, minWidth: 0, paddingTop: 3 }}>
            <Text style={s.caption}>
              {seg.say.slice(0, capN)}
              <Text style={s.caret}>▎</Text>
            </Text>
            <Text style={s.mode}>{voiceOn ? "SPEAKING · TAP NEXT TO SKIP" : "READING · VOICE OFF"}</Text>
          </View>
        </View>
        <View style={{ flexDirection: "row", gap: 9, marginTop: 15 }}>
          <Pressable style={s.showBtn} onPress={() => { onClose(); onOpenInbox(); }}>
            <Text style={s.showText}>{seg.showLabel}</Text>
          </Pressable>
          <Pressable style={s.nextBtn} onPress={next}>
            <Text style={s.nextText}>{seg.nextLabel}</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root: { ...StyleSheet.absoluteFillObject, backgroundColor: "#08080C", zIndex: 96 },
  field: {
    position: "absolute", top: -80, left: -60, right: -60, height: 380,
    borderBottomLeftRadius: 400, borderBottomRightRadius: 400, opacity: 0.9,
  },
  top: { paddingTop: 58, paddingHorizontal: 16 },
  barTrack: {
    flex: 1, height: 2.5, borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.22)", overflow: "hidden",
  },
  barFill: { height: "100%", backgroundColor: "#FFFFFF", borderRadius: 2 },
  agentRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14 },
  agentTile: { width: 28, height: 28, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  agentTileText: { fontFamily: SANS_SEMI, fontSize: 12, color: "#FFFFFF" },
  agentName: { fontFamily: SANS_SEMI, fontSize: 13, color: T },
  agentStamp: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 1.5, color: "rgba(244,242,250,0.62)", marginTop: 1 },
  mutePill: {
    flexDirection: "row", alignItems: "center", gap: 7,
    paddingHorizontal: 13, paddingVertical: 7, borderRadius: 100,
    backgroundColor: "rgba(199,184,255,0.14)", borderWidth: 1,
    borderColor: "rgba(199,184,255,0.35)",
  },
  mutePillOff: { backgroundColor: "rgba(255,255,255,0.05)", borderColor: "rgba(255,255,255,0.14)" },
  muteText: { fontFamily: MONO, fontSize: 10, letterSpacing: 1, color: "#C7B8FF" },
  body: { paddingHorizontal: 22, paddingTop: 22, paddingBottom: 8 },
  head: { fontFamily: SERIF, fontSize: 38, lineHeight: 42, color: T },
  statTile: {
    flex: 1, padding: 13, borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.05)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  statN: { fontFamily: SERIF, fontSize: 27, lineHeight: 28 },
  statLabel: { fontFamily: SANS, fontSize: 10.5, lineHeight: 14, color: "rgba(244,242,250,0.62)", marginTop: 5 },
  mailRow: {
    flexDirection: "row", alignItems: "center", gap: 11,
    paddingHorizontal: 14, paddingVertical: 13, borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.04)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  mailAvatar: { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  mailAvatarText: { fontFamily: SANS_SEMI, fontSize: 10.5, color: "#FFFFFF" },
  mailFrom: { fontFamily: SANS_SEMI, fontSize: 13, color: T },
  mailSub: { fontFamily: SANS, fontSize: 11, color: "rgba(244,242,250,0.6)", marginTop: 2 },
  mailChip: {
    paddingHorizontal: 9, paddingVertical: 5, borderRadius: 100,
    backgroundColor: "rgba(255,157,168,0.14)",
  },
  mailChipText: { fontFamily: MONO, fontSize: 10, color: "#FF9DA8" },
  noteRow: {
    flexDirection: "row", alignItems: "flex-start", gap: 11,
    paddingHorizontal: 14, paddingVertical: 13, borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.04)", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  noteDot: { width: 7, height: 7, borderRadius: 4, marginTop: 5, backgroundColor: "#C7B8FF" },
  noteFrom: { fontFamily: SANS_SEMI, fontSize: 12.5, color: T },
  noteGist: { fontFamily: SANS, fontSize: 11.5, lineHeight: 16.5, color: "rgba(244,242,250,0.6)", marginTop: 3 },
  ledgerRow: {
    flexDirection: "row", alignItems: "center", gap: 11,
    paddingHorizontal: 14, paddingVertical: 12, borderRadius: 16,
    backgroundColor: "rgba(255,255,255,0.04)", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  ledgerTile: { width: 26, height: 26, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  ledgerTileText: { fontFamily: SANS_SEMI, fontSize: 10.5, color: "#FFFFFF" },
  ledgerText: { flex: 1, fontFamily: SANS, fontSize: 12.5, lineHeight: 17.5, color: "rgba(244,242,250,0.82)" },
  ledgerN: { fontFamily: MONO, fontSize: 11, color: "rgba(124,247,196,0.9)" },
  bottom: { paddingHorizontal: 20, paddingBottom: 34 },
  orb: {
    width: 44, height: 44, borderRadius: 22,
    shadowColor: "#8F7BEB", shadowOpacity: 0.9, shadowRadius: 18,
    shadowOffset: { width: 0, height: 0 },
  },
  caption: { fontFamily: SANS, fontSize: 13.5, lineHeight: 21, color: "rgba(244,242,250,0.9)" },
  caret: { color: "#C7B8FF" },
  mode: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: "rgba(244,242,250,0.5)", marginTop: 7 },
  showBtn: {
    flex: 1, padding: 15, borderRadius: 100, alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.07)", borderWidth: 1, borderColor: "rgba(255,255,255,0.14)",
  },
  showText: { fontFamily: SANS_SEMI, fontSize: 14, color: T },
  nextBtn: { flex: 1, padding: 15, borderRadius: 100, alignItems: "center", backgroundColor: "#C7B8FF" },
  nextText: { fontFamily: SANS_SEMI, fontSize: 14, color: "#14101F" },
});
