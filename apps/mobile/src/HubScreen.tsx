// The Hub, design-true (Nano V4 (2)): serif greeting, the brief with stat
// tiles, a Context timeline where every signal ends in a verdict, the earned
// autonomy panel, and the app cards — inbox, nutrition, finance, stylist,
// flights — each one tap away. Consumes the existing /v1/screen/hub SDUI
// payload; only the presentation is native.
import { LinearGradient } from "expo-linear-gradient";
import React, { useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type {
  Action, ActionRow, AgentCard, AgentGrid, ListBlock, Screen as SduiScreenT,
  TextBlock, Timeline,
} from "./sdui/types";

const C = {
  bg: "#04040A",
  panel: "rgba(25,18,51,0.55)",
  panelSolid: "#12101C",
  border: "rgba(199,184,255,0.14)",
  text: "#F4F2FA",
  muted: "#8A87A3",
  lavender: "#C7B8FF",
  mint: "#7CF7C4",
  rose: "#FF9DA8",
  amber: "#FFD9A0",
  onAccent: "#14101F",
};
const MONO = "Menlo";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";

const TONE_DOT: Record<string, string> = { ask: C.rose, did: C.lavender, filed: C.mint };
const CARD_TONES: Record<string, { tile: string; letter: string }> = {
  mint: { tile: "rgba(124,247,196,0.16)", letter: C.mint },
  amber: { tile: "rgba(255,217,160,0.16)", letter: C.amber },
  rose: { tile: "rgba(255,157,168,0.16)", letter: C.rose },
  indigo: { tile: "rgba(199,184,255,0.16)", letter: C.lavender },
};

// A quiet deterministic starfield — same sky every render.
const STARS = Array.from({ length: 26 }, (_, i) => ({
  left: ((i * 137) % 100),
  top: ((i * 89 + 13) % 100),
  size: 1 + (i % 3 === 0 ? 1 : 0),
  opacity: 0.14 + ((i * 31) % 40) / 100,
}));

type Blocks = {
  stamp?: string;
  hint?: string;
  brief?: AgentCard;
  inbox?: AgentCard;
  connectRow?: ActionRow;
  bodyText?: string;
  timeline?: Timeline;
  timelineTitle?: string;
  autonomy?: { title: string; list: ListBlock; actions?: ActionRow; caption?: string };
  grid?: AgentGrid;
};

function pluck(screen: SduiScreenT): Blocks {
  const out: Blocks = {};
  for (const section of screen.sections ?? []) {
    const title = section.title ?? "";
    if (title.toLowerCase().startsWith("without asking")) {
      const list = section.blocks.find((b) => b.type === "list") as ListBlock | undefined;
      const actions = section.blocks.find((b) => b.type === "action_row") as ActionRow | undefined;
      const cap = section.blocks.find(
        (b) => b.type === "text" && (b as TextBlock).variant === "caption") as TextBlock | undefined;
      if (list) out.autonomy = { title, list, actions, caption: cap?.text };
      continue;
    }
    for (const b of section.blocks) {
      if (b.type === "text") {
        const t = b as TextBlock;
        if (t.variant === "caption" && !out.stamp && /·/.test(t.text) && t.text === t.text.toUpperCase()) {
          out.stamp = t.text;
        } else if (t.variant === "caption" && /orb/i.test(t.text)) out.hint = t.text;
        else if (t.variant === "body") out.bodyText = t.text;
      } else if (b.type === "agent_card") {
        const c = b as AgentCard;
        if (c.id === "morning-brief") out.brief = c;
        else if (c.id === "inbox-zero") out.inbox = c;
      } else if (b.type === "timeline") {
        out.timeline = b as Timeline;
        out.timelineTitle = title || undefined;
      } else if (b.type === "agent_grid") out.grid = b as AgentGrid;
      else if (b.type === "action_row") out.connectRow = b as ActionRow;
    }
  }
  return out;
}

export function HubScreen({
  screen,
  onNavigate,
  onReaction,
}: {
  screen: SduiScreenT;
  onNavigate: (screen: string) => void;
  onReaction: (kind: string, targetId: string, agent?: string) => void;
}) {
  const b = useMemo(() => pluck(screen), [screen]);
  const [line1, line2] = useMemo(() => {
    const t = screen.title ?? "";
    const i = t.indexOf(", ");
    return i > 0 ? [t.slice(0, i + 1), t.slice(i + 2)] : [t, ""];
  }, [screen.title]);
  const briefTime = (b.stamp ?? "").split("·")[1]?.trim() ?? "";

  return (
    <View style={s.root}>
      {STARS.map((st, i) => (
        <View key={i} pointerEvents="none" style={{
          position: "absolute", left: `${st.left}%`, top: `${st.top}%`,
          width: st.size, height: st.size, borderRadius: 1,
          backgroundColor: "#CDBFFF", opacity: st.opacity,
        }} />
      ))}

      {b.stamp ? <Text style={s.stamp}>{b.stamp}</Text> : null}
      <Text style={s.greeting}>{line1}</Text>
      {line2 ? <Text style={s.greeting}>{line2}</Text> : null}
      {b.hint ? <Text style={s.hint}>{b.hint}</Text> : null}

      {b.inbox ? (
        <Pressable style={s.brief} onPress={() => onNavigate("inbox")}>
          <View style={s.briefHead}>
            <LinearGradient colors={["#818CF8", "#4338CA"]}
                            start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.inboxTile}>
              <Text style={s.inboxTileText}>I</Text>
            </LinearGradient>
            <View style={{ flex: 1 }}>
              <Text style={s.inboxName}>Inbox Zero</Text>
              <Text style={s.inboxSub}>{b.inbox.sub}{briefTime ? ` · synced ${briefTime}` : ""}</Text>
            </View>
            <Text style={s.live}>LIVE</Text>
            <Text style={s.chevron}>›</Text>
          </View>
          <Text style={s.briefHeadline}>{b.inbox.headline}</Text>
          <Text style={s.briefBody}>{b.inbox.body}</Text>
          {b.inbox.stats?.length ? (
            <View style={s.statRow}>
              {b.inbox.stats.map((st, i) => (
                <View key={i} style={s.statTile}>
                  <Text style={[s.statN, st.accent && { color: C.mint }]}>{st.n}</Text>
                  <Text style={s.statLabel}>{st.label}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </Pressable>
      ) : null}

      {!b.brief && b.bodyText ? (
        <View style={s.brief}>
          <Text style={s.briefBody}>{b.bodyText}</Text>
          {b.connectRow?.actions.map((a: Action) => (
            <Pressable key={a.id} style={s.connectBtn}
                       onPress={() => onReaction("action_tapped", a.id)}>
              <Text style={s.connectText}>{a.label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      <View style={s.sectionHead}>
        <Text style={s.sectionTitle}>Your Hub</Text>
      </View>
      <View style={s.cards}>
        {b.grid?.items.map((g) => (
          <AppCard key={g.screen} letter={g.name[0]} tone={g.tone ?? "indigo"}
                   name={g.name} sub={g.sub} onPress={() => onNavigate(g.screen)} />
        ))}
        <AppCard letter="✈" tone="indigo" name="Flights"
                 sub="Fares watched daily" onPress={() => onNavigate("flights")} />
      </View>
    </View>
  );
}

function AppCard({ letter, tone, name, sub, onPress }: {
  letter: string; tone: string; name: string; sub: string; onPress: () => void;
}) {
  const t = CARD_TONES[tone] ?? CARD_TONES.indigo;
  return (
    <Pressable style={s.card} onPress={onPress}>
      <View style={[s.cardTile, { backgroundColor: t.tile }]}>
        <Text style={[s.cardLetter, { color: t.letter }]}>{letter}</Text>
      </View>
      <Text style={s.cardName}>{name}</Text>
      <Text style={s.cardSub} numberOfLines={2}>{sub}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  // The app's ScrollView already pads 16; add only a touch more breathing room.
  root: { paddingHorizontal: 4, paddingBottom: 40 },
  stamp: { fontFamily: MONO, fontSize: 11, letterSpacing: 3, color: C.muted, marginTop: 8 },
  greeting: { fontFamily: SERIF, fontSize: 42, lineHeight: 48, color: C.text },
  hint: { fontFamily: SANS, fontSize: 13, color: C.lavender, marginTop: 10 },
  brief: {
    marginTop: 24, borderRadius: 24, borderWidth: 1, borderColor: C.border,
    backgroundColor: C.panel, padding: 20,
  },
  briefHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  orbDot: {
    width: 18, height: 18, borderRadius: 9, backgroundColor: "#7C6CFF",
    shadowColor: "#9F8CFF", shadowOpacity: 0.9, shadowRadius: 8, shadowOffset: { width: 0, height: 0 },
  },
  briefLabel: { fontFamily: MONO, fontSize: 11, letterSpacing: 3, color: C.muted, flex: 1 },
  inboxTile: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  inboxTileText: { fontFamily: SANS_SEMI, fontSize: 18, color: "#FFFFFF" },
  inboxName: { fontFamily: SANS_SEMI, fontSize: 17, color: C.text },
  inboxSub: { fontFamily: SANS, fontSize: 12.5, color: C.muted, marginTop: 2 },
  live: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.mint, marginRight: 2 },
  chevron: { color: C.muted, fontSize: 22, lineHeight: 22 },
  briefHeadline: { fontFamily: SERIF, fontSize: 27, lineHeight: 33, color: C.text, marginTop: 14 },
  briefBody: { fontFamily: SANS, fontSize: 15, lineHeight: 22, color: "#B9B4CC", marginTop: 10 },
  statRow: { flexDirection: "row", gap: 10, marginTop: 18 },
  statTile: {
    flex: 1, borderRadius: 16, borderWidth: 1, borderColor: C.border,
    backgroundColor: "rgba(4,4,10,0.5)", padding: 12,
  },
  statN: { fontFamily: SERIF, fontSize: 24, color: C.text },
  statLabel: { fontFamily: SANS, fontSize: 12, lineHeight: 16, color: C.muted, marginTop: 4 },
  connectBtn: {
    marginTop: 14, backgroundColor: C.lavender, borderRadius: 999,
    paddingVertical: 12, alignItems: "center",
  },
  connectText: { fontFamily: SANS_SEMI, fontSize: 14, color: C.onAccent },
  sectionHead: {
    flexDirection: "row", alignItems: "baseline", justifyContent: "space-between",
    marginTop: 34, marginBottom: 12,
  },
  sectionTitle: { fontFamily: SERIF, fontSize: 25, color: C.text },
  sectionAside: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, color: C.mint },
  panel: {
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    backgroundColor: C.panel, padding: 16,
  },
  tlItem: { flexDirection: "row", gap: 12, paddingVertical: 10 },
  tlDivider: { borderTopWidth: 1, borderTopColor: "rgba(199,184,255,0.08)" },
  tlDot: { width: 7, height: 7, borderRadius: 4, marginTop: 6 },
  tlText: { fontFamily: SANS, fontSize: 15, lineHeight: 21, color: C.text },
  tlVerdict: { fontFamily: MONO, fontSize: 10, letterSpacing: 2, marginTop: 5 },
  tlAt: { fontFamily: MONO, fontSize: 11, color: C.muted, marginTop: 2 },
  tlFooter: { fontFamily: SANS, fontSize: 13, lineHeight: 19, color: C.muted, marginTop: 12 },
  autoItem: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 11 },
  autoTitle: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  autoSub: { fontFamily: SANS, fontSize: 12, lineHeight: 17, color: C.muted, marginTop: 3 },
  chip: { borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5, borderWidth: 1 },
  chipAuto: { borderColor: "rgba(124,247,196,0.4)", backgroundColor: "rgba(124,247,196,0.08)" },
  chipAsks: { borderColor: "rgba(199,184,255,0.4)", backgroundColor: "rgba(199,184,255,0.08)" },
  chipText: { fontFamily: MONO, fontSize: 9, letterSpacing: 1.5 },
  promoteBtn: {
    marginTop: 12, borderRadius: 999, borderWidth: 1, borderColor: C.lavender,
    paddingVertical: 11, alignItems: "center",
  },
  promoteText: { fontFamily: SANS_SEMI, fontSize: 13, color: C.lavender },
  cards: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  card: {
    width: "47.5%", borderRadius: 20, borderWidth: 1, borderColor: C.border,
    backgroundColor: C.panel, padding: 16,
  },
  cardTile: {
    width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center",
  },
  cardLetter: { fontFamily: SANS_SEMI, fontSize: 18 },
  cardName: { fontFamily: SANS_SEMI, fontSize: 16, color: C.text, marginTop: 12 },
  cardSub: { fontFamily: SANS, fontSize: 12, lineHeight: 17, color: C.muted, marginTop: 4 },
});
