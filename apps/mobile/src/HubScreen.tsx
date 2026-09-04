// The Hub, matched to Nano V1 (6): "My Hub" serif title, the lavender
// Morning briefing card (Play -> the full-screen brief player), the Inbox
// Zero card with serif headline + stat tiles, and the app grid. Consumes
// the existing /v1/screen/hub SDUI payload; presentation is native.
import { LinearGradient } from "expo-linear-gradient";
import React, { useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type {
  Action, ActionRow, AgentCard, AgentGrid, Screen as SduiScreenT, TextBlock,
} from "./sdui/types";

const C = {
  text: "#F4F2FA",
  muted: "#8A87A3",
  lavender: "#C7B8FF",
  mint: "#7CF7C4",
  border: "rgba(199,184,255,0.14)",
};
const MONO = "JetBrainsMono_400Regular";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";

const STAT_COLORS = ["#7CF7C4", "#C7B8FF", "#F4F2FA"];
const APP_GRADS: Record<string, [string, string]> = {
  mint: ["#4ADE80", "#15803D"],
  amber: ["#FBBF24", "#B45309"],
  rose: ["#F87171", "#B91C1C"],
  indigo: ["#818CF8", "#4338CA"],
};

const STARS = Array.from({ length: 22 }, (_, i) => ({
  left: ((i * 137) % 100),
  top: ((i * 89 + 13) % 100),
  size: 1 + (i % 3 === 0 ? 1 : 0),
  opacity: 0.12 + ((i * 31) % 36) / 100,
}));

type Blocks = {
  stamp?: string;
  hint?: string;
  inbox?: AgentCard;
  connectRow?: ActionRow;
  bodyText?: string;
  grid?: AgentGrid;
};

function pluck(screen: SduiScreenT): Blocks {
  const out: Blocks = {};
  for (const section of screen.sections ?? []) {
    if ((section.title ?? "").toLowerCase().startsWith("without asking")) continue;
    for (const b of section.blocks) {
      if (b.type === "text") {
        const t = b as TextBlock;
        if (t.variant === "caption" && !out.stamp && /\u00b7/.test(t.text) && t.text === t.text.toUpperCase()) {
          out.stamp = t.text;
        } else if (t.variant === "caption" && /orb/i.test(t.text)) out.hint = t.text;
        else if (t.variant === "body") out.bodyText = t.text;
      } else if (b.type === "agent_card") {
        const c = b as AgentCard;
        if (c.id === "inbox-zero") out.inbox = c;
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
  onPlayBrief,
}: {
  screen: SduiScreenT;
  onNavigate: (screen: string) => void;
  onReaction: (kind: string, targetId: string, agent?: string) => void;
  onPlayBrief: () => void;
}) {
  const b = useMemo(() => pluck(screen), [screen]);
  const initial = useMemo(() => {
    const m = (screen.title ?? "").match(/, (.+)\.$/);
    return (m?.[1] ?? "N").slice(0, 1).toUpperCase();
  }, [screen.title]);
  const syncedAt = (b.stamp ?? "").split("\u00b7")[1]?.trim().toLowerCase() ?? "";

  return (
    <View style={s.root}>
      {STARS.map((st, i) => (
        <View key={i} pointerEvents="none" style={{
          position: "absolute", left: `${st.left}%`, top: `${st.top}%`,
          width: st.size, height: st.size, borderRadius: 1,
          backgroundColor: "#CDBFFF", opacity: st.opacity,
        }} />
      ))}

      <View style={s.topRow}>
        {b.stamp ? <Text style={s.stamp}>{b.stamp}</Text> : <View />}
        <Pressable onPress={() => onNavigate("profile")} hitSlop={10}>
          <View style={s.avatarWrap}>
            <LinearGradient colors={["#C7B8FF", "#6D5BD0"]}
                            start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.avatarChip}>
              <Text style={s.avatarChipText}>{initial}</Text>
            </LinearGradient>
            <View style={s.avatarDot} />
          </View>
        </Pressable>
      </View>
      <Text style={s.title}>My Hub</Text>
      {b.hint ? <Text style={s.hint}>{b.hint}</Text> : null}

      {b.inbox ? (
        <Pressable onPress={onPlayBrief}>
          <LinearGradient
            colors={["rgba(139,123,240,0.9)", "rgba(58,44,110,0.92)"]}
            start={{ x: 0.1, y: 0 }} end={{ x: 0.9, y: 1 }}
            style={s.briefing}
          >
            <View style={s.briefGlow} pointerEvents="none" />
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <View style={s.readyDot} />
              <Text style={s.readyText}>READY FOR YOU</Text>
            </View>
            <Text style={s.briefTitle}>Morning briefing</Text>
            <Text style={s.briefSub}>
              Your inbox, in forty seconds. Read it or let me say it.
            </Text>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 9, marginTop: 15 }}>
              <View style={s.playPill}><Text style={s.playText}>Play</Text></View>
              <Text style={s.playMeta}>INBOX ONLY · 40 SEC</Text>
            </View>
          </LinearGradient>
        </Pressable>
      ) : null}

      {b.inbox ? (
        <Pressable onPress={() => onNavigate("inbox")}>
          <LinearGradient
            colors={["rgba(129,140,248,0.30)", "rgba(67,56,202,0.16)"]}
            start={{ x: 0.1, y: 0 }} end={{ x: 0.9, y: 1 }}
            style={s.inboxCard}
          >
            <View style={{ flexDirection: "row", alignItems: "center", gap: 11 }}>
              <LinearGradient colors={["#818CF8", "#4338CA"]}
                              start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.inboxTile}>
                <Text style={s.inboxTileText}>I</Text>
              </LinearGradient>
              <View style={{ flex: 1 }}>
                <Text style={s.inboxName}>Inbox Zero</Text>
                <Text style={s.inboxSub}>
                  {b.inbox.sub}{syncedAt ? ` \u00b7 synced ${syncedAt}` : ""}
                </Text>
              </View>
              <Text style={s.live}>LIVE</Text>
              <Text style={s.chevron}>›</Text>
            </View>
            <Text style={s.inboxHeadline}>{b.inbox.headline}</Text>
            <Text style={s.inboxBody}>{b.inbox.body}</Text>
            {b.inbox.stats?.length ? (
              <View style={{ flexDirection: "row", gap: 8, marginTop: 15 }}>
                {b.inbox.stats.map((st, i) => (
                  <View key={i} style={s.statTile}>
                    <Text style={[s.statN, { color: STAT_COLORS[i] ?? C.text }]}>{st.n}</Text>
                    <Text style={s.statLabel}>{st.label}</Text>
                  </View>
                ))}
              </View>
            ) : null}
          </LinearGradient>
        </Pressable>
      ) : null}

      {!b.inbox && b.bodyText ? (
        <View style={s.connectCard}>
          <Text style={s.inboxBody}>{b.bodyText}</Text>
          {b.connectRow?.actions.map((a: Action) => (
            <Pressable key={a.id} style={s.connectBtn}
                       onPress={() => onReaction("action_tapped", a.id)}>
              <Text style={s.connectText}>{a.label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

      <Text style={s.sectionTitle}>Your Hub</Text>
      <View style={s.cards}>
        {(b.grid?.items ?? []).map((g) => (
          <Pressable key={g.screen} style={s.card} onPress={() => onNavigate(g.screen)}>
            <LinearGradient colors={APP_GRADS[g.tone ?? "indigo"] ?? APP_GRADS.indigo}
                            start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.cardTile}>
              <Text style={s.cardTileText}>{g.name[0]}</Text>
            </LinearGradient>
            <Text style={s.cardName}>{g.name}</Text>
            <Text style={s.cardSub} numberOfLines={1}>{g.sub.toUpperCase()}</Text>
          </Pressable>
        ))}
        <Pressable style={s.card} onPress={() => onNavigate("flights")}>
          <LinearGradient colors={APP_GRADS.indigo}
                          start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.cardTile}>
            <Text style={s.cardTileText}>{"\u2708"}</Text>
          </LinearGradient>
          <Text style={s.cardName}>Flights</Text>
          <Text style={s.cardSub}>FARES WATCHED DAILY</Text>
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root: { paddingHorizontal: 4, paddingBottom: 130 },
  topRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  stamp: { fontFamily: MONO, fontSize: 11, letterSpacing: 3, color: C.muted, marginTop: 8 },
  avatarWrap: { width: 40, height: 40 },
  avatarChip: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center" },
  avatarChipText: { fontFamily: SANS_SEMI, fontSize: 15, color: "#14101F" },
  avatarDot: {
    position: "absolute", right: 0, bottom: 0, width: 10, height: 10,
    borderRadius: 5, backgroundColor: C.mint, borderWidth: 2, borderColor: "#0B0910",
  },
  title: { fontFamily: SERIF, fontSize: 42, lineHeight: 48, color: C.text, marginTop: 6 },
  hint: { fontFamily: SANS, fontSize: 13, color: C.lavender, marginTop: 8 },
  briefing: {
    marginTop: 20, padding: 18, borderRadius: 24, overflow: "hidden",
    borderWidth: 1, borderColor: "rgba(199,184,255,0.3)",
  },
  briefGlow: {
    position: "absolute", right: -40, bottom: -52, width: 170, height: 170,
    borderRadius: 85, backgroundColor: "rgba(255,255,255,0.13)",
  },
  readyDot: {
    width: 6, height: 6, borderRadius: 3, backgroundColor: C.mint,
    shadowColor: C.mint, shadowOpacity: 1, shadowRadius: 8, shadowOffset: { width: 0, height: 0 },
  },
  readyText: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 2.5, color: "rgba(255,255,255,0.78)" },
  briefTitle: { fontFamily: SERIF, fontSize: 27, lineHeight: 30, color: "#FFFFFF", marginTop: 9 },
  briefSub: { fontFamily: SANS, fontSize: 13, lineHeight: 19.5, color: "rgba(255,255,255,0.72)", marginTop: 6 },
  playPill: {
    paddingHorizontal: 15, paddingVertical: 9, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.16)", borderWidth: 1, borderColor: "rgba(255,255,255,0.24)",
  },
  playText: { fontFamily: SANS_SEMI, fontSize: 12.5, color: "#FFFFFF" },
  playMeta: { fontFamily: MONO, fontSize: 10, letterSpacing: 1.5, color: "rgba(255,255,255,0.6)" },
  inboxCard: {
    marginTop: 12, padding: 17, borderRadius: 24,
    borderWidth: 1, borderColor: "rgba(199,184,255,0.26)",
  },
  inboxTile: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  inboxTileText: { fontFamily: SANS_SEMI, fontSize: 17, color: "#FFFFFF" },
  inboxName: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  inboxSub: { fontFamily: SANS, fontSize: 11.5, color: "rgba(244,242,250,0.6)", marginTop: 2 },
  live: { fontFamily: MONO, fontSize: 10.5, letterSpacing: 1.5, color: "rgba(124,247,196,0.9)" },
  chevron: { color: "rgba(244,242,250,0.5)", fontSize: 20, lineHeight: 22, marginLeft: 2 },
  inboxHeadline: { fontFamily: SERIF, fontSize: 25, lineHeight: 28.5, color: C.text, marginTop: 14 },
  inboxBody: { fontFamily: SANS, fontSize: 13, lineHeight: 19.5, color: "rgba(244,242,250,0.66)", marginTop: 7 },
  statTile: {
    flex: 1, paddingHorizontal: 12, paddingVertical: 11, borderRadius: 15,
    backgroundColor: "rgba(0,0,0,0.22)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  statN: { fontFamily: SERIF, fontSize: 22, lineHeight: 23 },
  statLabel: { fontFamily: SANS, fontSize: 10.5, lineHeight: 14, color: "rgba(244,242,250,0.62)", marginTop: 5 },
  connectCard: {
    marginTop: 20, padding: 18, borderRadius: 24,
    borderWidth: 1, borderColor: C.border, backgroundColor: "rgba(25,18,51,0.45)",
  },
  connectBtn: {
    marginTop: 14, backgroundColor: C.lavender, borderRadius: 999,
    paddingVertical: 12, alignItems: "center",
  },
  connectText: { fontFamily: SANS_SEMI, fontSize: 14, color: "#14101F" },
  sectionTitle: { fontFamily: SANS_SEMI, fontSize: 16, color: C.text, marginTop: 28, marginBottom: 11 },
  cards: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  card: {
    width: "48.4%", padding: 14, borderRadius: 20,
    backgroundColor: "rgba(255,255,255,0.025)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
  },
  cardTile: { width: 28, height: 28, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  cardTileText: { fontFamily: SANS_SEMI, fontSize: 11, color: "#FFFFFF" },
  cardName: { fontFamily: SANS_SEMI, fontSize: 12.5, color: "rgba(244,242,250,0.9)", marginTop: 10 },
  cardSub: { fontFamily: MONO, fontSize: 9.5, letterSpacing: 1, color: "rgba(244,242,250,0.42)", marginTop: 4 },
});
