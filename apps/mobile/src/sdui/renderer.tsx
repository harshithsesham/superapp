// The thin renderer (architecture §3): agents choose WHICH components and WHAT
// content; how they look was decided once, here. Adding a component to the
// registry = adding a case to this switch + a type in types.ts + blocks.py.
import React, { useState } from "react";
import { Image, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Circle } from "react-native-svg";
import type { LeafBlock, Screen, Section } from "./types";

type ReactionFn = (kind: string, targetId: string, agent?: string) => void;

// Media served by the API is auth-gated and returned as relative URLs.
type MediaCtx = { baseUrl: string; headers?: Record<string, string> };

export type DraftActionFn = (
  action: "send" | "defer" | "save" | "now",
  draftId: string,
  body?: string
) => void;

export type NavigateFn = (screen: string) => void;

type RenderCtx = {
  onReaction: ReactionFn;
  media?: MediaCtx;
  onDraftAction?: DraftActionFn;
  onNavigate?: NavigateFn;
  onFix?: (id: string) => void;
  dark?: boolean;
};

export function SduiScreen({
  screen,
  onReaction,
  media,
  onDraftAction,
  onNavigate,
  onFix,
}: {
  screen: Screen;
  onReaction: ReactionFn;
  media?: MediaCtx;
  onDraftAction?: DraftActionFn;
  onNavigate?: NavigateFn;
  onFix?: (id: string) => void;
}) {
  const dark = screen.theme === "dark";
  const ctx: RenderCtx = { onReaction, media, onDraftAction, onNavigate, onFix, dark };
  return (
    <View>
      <Text style={[s.screenTitle, dark && dk.screenTitle]}>{screen.title}</Text>
      {screen.sections.map((section, i) => (
        <SduiSection key={i} section={section} ctx={ctx} />
      ))}
    </View>
  );
}

function SduiSection({ section, ctx }: { section: Section; ctx: RenderCtx }) {
  return (
    <View style={s.section}>
      {section.title ? (
        <Text style={[s.sectionTitle, ctx.dark && dk.sectionTitle]}>{section.title}</Text>
      ) : null}
      {section.blocks.map((block, i) => (
        <Block key={i} block={block} ctx={ctx} />
      ))}
    </View>
  );
}

function AgentCardView({ block }: { block: Extract<LeafBlock, { type: "agent_card" }> }) {
  return (
    <LinearGradient
      colors={["#2A2050", "#140F26", "#08070E"]}
      start={{ x: 0.2, y: 0 }}
      end={{ x: 0.6, y: 1 }}
      style={ac.card}
    >
      <View style={ac.header}>
        <LinearGradient colors={["#818CF8", "#4338CA"]} style={ac.tile}>
          <Text style={ac.tileText}>{block.name[0]}</Text>
        </LinearGradient>
        <View style={{ flex: 1, marginLeft: 10 }}>
          <Text style={ac.name}>{block.name}</Text>
          <Text style={ac.sub}>{block.sub}</Text>
        </View>
        {block.live ? <Text style={ac.live}>LIVE</Text> : null}
      </View>
      <Text style={ac.headline}>{block.headline}</Text>
      <Text style={ac.body}>{block.body}</Text>
      {block.stats?.length ? (
        <View style={ac.statRow}>
          {(block.stats ?? []).map((st, i) => (
            <View key={i} style={ac.statCell}>
              <Text style={[ac.statN, st.accent && ac.statAccent]}>{st.n}</Text>
              <Text style={ac.statLabel}>{st.label.toUpperCase()}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </LinearGradient>
  );
}

const ac = StyleSheet.create({
  card: {
    borderRadius: 24,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.16)",
    padding: 18,
    marginTop: 10,
  },
  header: { flexDirection: "row", alignItems: "center" },
  tile: { width: 34, height: 34, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  tileText: { fontFamily: "InstrumentSerif_400Regular", fontSize: 17, color: "#FFFFFF" },
  name: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 15, color: "#F4F2FA" },
  sub: { fontFamily: "InstrumentSans_400Regular", fontSize: 11, color: "#8A87A3", marginTop: 1 },
  live: { fontFamily: "JetBrainsMono_400Regular", fontSize: 10, color: "#7CF7C4", letterSpacing: 2 },
  headline: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 27,
    lineHeight: 32,
    color: "#F4F2FA",
    marginTop: 14,
  },
  body: {
    fontFamily: "InstrumentSans_400Regular",
    fontSize: 13,
    lineHeight: 19,
    color: "#B9B4CC",
    marginTop: 6,
  },
  statRow: { flexDirection: "row", gap: 8, marginTop: 14 },
  statCell: {
    flex: 1,
    backgroundColor: "rgba(8,7,14,0.55)",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    borderRadius: 12,
    padding: 10,
  },
  statN: { fontFamily: "InstrumentSerif_400Regular", fontSize: 22, color: "#F4F2FA" },
  statAccent: { color: "#7CF7C4" },
  statLabel: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 8,
    letterSpacing: 0.8,
    color: "#8A87A3",
    marginTop: 4,
  },
});

const ag = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 6 },
  cell: {
    width: "47.5%",
    backgroundColor: "#14101F",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    borderRadius: 18,
    padding: 14,
  },
  tile: {
    width: 30, height: 30, borderRadius: 9,
    alignItems: "center", justifyContent: "center", marginBottom: 10,
  },
  tile_indigo: { backgroundColor: "#5B49C9" },
  tile_mint: { backgroundColor: "#1E7A5C" },
  tile_amber: { backgroundColor: "#8A6420" },
  tile_rose: { backgroundColor: "#A34B60" },
  tileText: { fontFamily: "InstrumentSerif_400Regular", fontSize: 15, color: "#FFFFFF" },
  name: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 14, color: "#F4F2FA" },
  sub: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 11.5,
    color: "#8A87A3", marginTop: 3, lineHeight: 15,
  },
});

function DraftCardView({
  block,
  onDraftAction,
  dark,
}: {
  block: Extract<LeafBlock, { type: "draft_card" }>;
  onDraftAction?: DraftActionFn;
  dark?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(block.draft);
  const [showWhy, setShowWhy] = useState(false);
  const deferred = !!block.deferred_label;
  return (
    <View style={[s.draftCard, dark && dk.card, deferred && { opacity: 0.62 }]}>
      <View style={s.draftTop}>
        <Text style={[s.draftFrom, dark && dk.ink]}>{block.from_name}</Text>
        {block.why ? (
          <Text style={[s.whyChip, dark && dk.whyChip]}>{block.why.toUpperCase()}</Text>
        ) : null}
      </View>
      <Text style={[s.draftSubject, dark && dk.caption]}>{block.subject}</Text>
      <Text style={[s.draftLabel, dark && dk.mono, deferred && { color: "#7CF7C4" }]}>
        {deferred ? block.deferred_label : "WRITTEN AND WAITING"}
      </Text>
      {editing ? (
        <TextInput
          style={[s.draftEdit, dark && dk.draftEdit]}
          value={text}
          onChangeText={setText}
          multiline
          autoFocus
        />
      ) : (
        <Text style={[s.draftBody, dark && dk.draftBody]}>{text}</Text>
      )}
      {block.why_detail ? (
        <Pressable onPress={() => setShowWhy((v) => !v)}>
          <Text style={[s.draftLabel, dark && dk.mono, { color: "#C7B8FF" }]}>
            {showWhy ? "WHY I WROTE THIS —" : "WHY I WROTE THIS +"}
          </Text>
          {showWhy ? (
            <Text style={[s.caption, dark && dk.caption, { marginTop: 4 }]}>
              {block.why_detail}
            </Text>
          ) : null}
        </Pressable>
      ) : null}
      <View style={s.outfitActions}>
        {deferred ? (
          <Pressable
            style={[s.voteButton, s.voteButtonMuted, dark && dk.voteButtonMuted]}
            onPress={() => onDraftAction?.("now", block.id)}
          >
            <Text style={[s.voteText, s.voteTextMuted, dark && dk.caption]}>Answer now</Text>
          </Pressable>
        ) : editing ? (
          <>
            <Pressable
              style={[s.voteButton, dark && dk.voteButton]}
              onPress={() => {
                setEditing(false);
                onDraftAction?.("save", block.id, text);
              }}
            >
              <Text style={[s.voteText, dark && dk.voteText]}>Save</Text>
            </Pressable>
            <Pressable
              style={[s.voteButton, s.voteButtonMuted, dark && dk.voteButtonMuted]}
              onPress={() => {
                setText(block.draft);
                setEditing(false);
              }}
            >
              <Text style={[s.voteText, s.voteTextMuted, dark && dk.caption]}>Cancel</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Pressable
              style={[s.voteButton, dark && dk.voteButton]}
              onPress={() => onDraftAction?.("send", block.id)}
            >
              <Text style={[s.voteText, dark && dk.voteText]}>Send it</Text>
            </Pressable>
            <Pressable
              style={[s.voteButton, s.voteButtonMuted, dark && dk.voteButtonMuted]}
              onPress={() => setEditing(true)}
            >
              <Text style={[s.voteText, s.voteTextMuted, dark && dk.caption]}>Change it</Text>
            </Pressable>
            <Pressable
              style={[s.voteButton, s.voteButtonMuted, dark && dk.voteButtonMuted]}
              onPress={() => onDraftAction?.("defer", block.id)}
            >
              <Text style={[s.voteText, s.voteTextMuted, dark && dk.caption]}>Ask at 6pm</Text>
            </Pressable>
          </>
        )}
      </View>
    </View>
  );
}

function ExpandableRow({
  item,
  dark,
  onFix,
}: {
  item: {
    id: string;
    title: string;
    subtitle?: string | null;
    trailing?: string | null;
    detail?: string | null;
    tile?: string | null;
    fixable_id?: string | null;
  };
  dark?: boolean;
  onFix?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const readable = !!item.detail;
  return (
    <Pressable onPress={readable ? () => setOpen((v) => !v) : undefined}>
      <View style={s.listItem}>
        {item.tile ? (
          <View style={cn.tile}>
            <Text style={cn.tileText}>{item.tile}</Text>
          </View>
        ) : null}
        <View style={{ flex: 1 }}>
          <Text style={[s.body, dark && dk.ink]}>{item.title}</Text>
          {item.subtitle ? (
            <Text style={[s.caption, dark && dk.caption, item.tile ? dk.mono : null]}>
              {item.subtitle}
            </Text>
          ) : null}
        </View>
        {item.trailing ? (
          <Text style={[s.trailing, dark && dk.body]}>{item.trailing}</Text>
        ) : readable ? (
          <Text style={[s.caption, dark && dk.caption]}>{open ? "−" : "+"}</Text>
        ) : null}
      </View>
      {open && item.detail ? (
        <View style={{ paddingBottom: 12 }}>
          <Text style={[s.caption, dark && dk.caption, { lineHeight: 19 }]}>{item.detail}</Text>
          {item.fixable_id && onFix ? (
            <Pressable onPress={() => onFix(item.fixable_id as string)} hitSlop={8}>
              <Text style={cn.fixLink}>✦ FIX THIS ESTIMATE</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const cn = StyleSheet.create({
  chip: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 2,
    color: "#C7B8FF",
  },
  dayCell: {
    width: 46,
    alignItems: "center",
    paddingVertical: 8,
    borderRadius: 12,
    backgroundColor: "#14101F",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.08)",
  },
  dayCellToday: {
    backgroundColor: "#221B3A",
    borderColor: "rgba(199,184,255,0.45)",
  },
  dayLetter: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 8,
    color: "#8A87A3",
  },
  dayNum: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 13,
    color: "#B9B4CC",
    marginTop: 2,
  },
  dayTextToday: { color: "#E9E4FF" },
  dayDot: {
    width: 10,
    height: 2,
    borderRadius: 1,
    backgroundColor: "#8B7CF6",
    marginTop: 4,
  },
  heroCard: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 22,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.16)",
    padding: 18,
    marginTop: 12,
  },
  heroBig: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 44,
    color: "#F4F2FA",
    lineHeight: 48,
  },
  heroLabel: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 1.8,
    color: "#8A87A3",
    marginTop: 2,
  },
  heroChip: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    color: "#B9B4CC",
  },
  ringPct: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 18,
    color: "#F4F2FA",
  },
  ringLabel: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 7,
    letterSpacing: 1.5,
    color: "#8A87A3",
  },
  tile: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: "#2A2050",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 12,
  },
  tileText: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 16,
    color: "#C7B8FF",
  },
  fixLink: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 1.6,
    color: "#C7B8FF",
    paddingVertical: 8,
  },
});

const METER_TONES: Record<string, string> = {
  mint: "#7CF7C4",
  lavender: "#C7B8FF",
  rose: "#FF9DA8",
  amber: "#F5C97B",
};

const mt = StyleSheet.create({
  label: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 1.6,
    color: "#8A87A3",
  },
  left: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    letterSpacing: 0.4,
  },
  track: {
    height: 3,
    borderRadius: 2,
    backgroundColor: "rgba(199,184,255,0.14)",
    overflow: "hidden",
  },
  fill: { height: 3, borderRadius: 2 },
});

function DayStripView({ block }: { block: Extract<LeafBlock, { type: "day_strip" }> }) {
  const scrollRef = React.useRef<ScrollView>(null);
  return (
    <View>
      {block.chip ? (
        <Text style={[cn.chip, { alignSelf: "flex-end", marginBottom: 8 }]}>● {block.chip}</Text>
      ) : null}
      <ScrollView
        ref={scrollRef}
        horizontal
        showsHorizontalScrollIndicator={false}
        onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: false })}
        contentContainerStyle={{ gap: 6 }}
      >
        {block.days.map((d, i) => (
          <View key={i} style={[cn.dayCell, d.today && cn.dayCellToday]}>
            <Text style={[cn.dayLetter, d.today && cn.dayTextToday]}>{d.letter}</Text>
            <Text style={[cn.dayNum, d.today && cn.dayTextToday]}>{d.num}</Text>
            <View style={[cn.dayDot, { opacity: d.logged ? 1 : 0 }]} />
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const TONE_COLORS: Record<string, string> = {
  ask: "#FF9DA8",
  did: "#C7B8FF",
  filed: "#8B87A0",
};

function TimelineView({
  block,
  dark,
}: {
  block: Extract<LeafBlock, { type: "timeline" }>;
  dark?: boolean;
}) {
  return (
    <View style={[tl.wrap, dark && tl.wrapDark]}>
      {block.items.map((item, i) => (
        <View key={i} style={[tl.row, i > 0 && tl.rowBorder, i > 0 && dark && tl.rowBorderDark]}>
          <Text style={tl.time}>{item.at}</Text>
          <View style={tl.mid}>
            <Text style={[tl.text, dark && dk.ink]} numberOfLines={2}>
              {item.text}
            </Text>
            <Text style={[tl.verdict, { color: TONE_COLORS[item.tone ?? "filed"] ?? TONE_COLORS.filed }]}>
              {item.verdict}
            </Text>
          </View>
        </View>
      ))}
      {block.footer ? <Text style={tl.footer}>{block.footer}</Text> : null}
    </View>
  );
}

function Block({ block, ctx }: { block: LeafBlock; ctx: RenderCtx }) {
  const { onReaction, media, dark } = ctx;
  switch (block.type) {
    case "text": {
      const style =
        block.variant === "title"
          ? s.title
          : block.variant === "subtitle"
          ? s.subtitle
          : block.variant === "caption"
          ? s.caption
          : s.body;
      const darkStyle =
        block.variant === "title"
          ? dk.title
          : block.variant === "subtitle"
          ? dk.subtitle
          : block.variant === "caption"
          ? dk.caption
          : dk.body;
      return <Text style={[style, dark && darkStyle]}>{block.text}</Text>;
    }

    case "insight_card": {
      const accent =
        block.emphasis === "positive" ? "#1E7F4F" : block.emphasis === "warning" ? "#B3261E" : "#3B3B3B";
      return (
        <View style={[s.card, dark && dk.card, { borderLeftColor: accent }]}>
          <View style={s.cardHeader}>
            <Text style={[s.cardTitle, dark && dk.ink]}>{block.title}</Text>
            <Pressable
              hitSlop={12}
              onPress={() => onReaction("insight_dismissed", block.id, block.agent)}
            >
              <Text style={s.dismiss}>✕</Text>
            </Pressable>
          </View>
          <Text style={[s.cardBody, dark && dk.bodyText]}>{block.body}</Text>
          {block.action_label && block.action_id ? (
            <Pressable
              style={s.cardAction}
              onPress={() => onReaction("action_tapped", block.action_id!, block.agent)}
            >
              <Text style={s.cardActionText}>{block.action_label}</Text>
            </Pressable>
          ) : null}
          <Text style={s.agentTag}>{block.agent}</Text>
        </View>
      );
    }

    case "stat_row":
      return (
        <View style={[s.statRow, dark && dk.card]}>
          {block.stats.map((stat, i) => (
            <View key={i} style={[s.stat, dark && dk.statCell]}>
              <Text style={[s.statValue, dark && dk.ink]}>
                {stat.value}
                {stat.unit ? <Text style={s.statUnit}> {stat.unit}</Text> : null}
              </Text>
              {stat.delta ? (
                <Text style={[s.statDelta, { color: stat.delta.startsWith("-") ? "#B3261E" : "#1E7F4F" }]}>
                  {stat.delta}
                </Text>
              ) : null}
              <Text style={[s.statLabel, dark && dk.mono]}>{stat.label}</Text>
            </View>
          ))}
        </View>
      );

    case "image_card": {
      const uri = block.image_url.startsWith("/")
        ? `${media?.baseUrl ?? ""}${block.image_url}`
        : block.image_url;
      return (
        <View style={s.imageCard}>
          <Image source={{ uri, headers: media?.headers }} style={s.image} resizeMode="cover" />
          {block.title ? <Text style={s.cardTitle}>{block.title}</Text> : null}
          {block.subtitle ? <Text style={s.caption}>{block.subtitle}</Text> : null}
        </View>
      );
    }

    case "list":
      return (
        <View style={[s.card, dark && dk.card]}>
          {block.items.map((item) => (
            <ExpandableRow key={item.id} item={item} dark={dark} onFix={ctx.onFix} />
          ))}
        </View>
      );

    case "image_grid": {
      const cols = block.columns ?? 3;
      return (
        <View style={s.grid}>
          {block.items.map((item) => {
            const uri = item.image_url.startsWith("/")
              ? `${media?.baseUrl ?? ""}${item.image_url}`
              : item.image_url;
            return (
              <View key={item.id} style={[s.gridCell, { width: `${100 / cols - 2}%` }]}>
                <Image source={{ uri, headers: media?.headers }} style={s.gridImage} resizeMode="cover" />
                {item.label ? (
                  <Text style={s.gridLabel} numberOfLines={1}>
                    {item.label}
                  </Text>
                ) : null}
              </View>
            );
          })}
        </View>
      );
    }

    case "outfit_card":
      return (
        <View style={s.outfitCard}>
          <View style={s.outfitHeader}>
            <View style={{ flex: 1 }}>
              <Text style={s.outfitTitle}>{block.title}</Text>
              {block.occasion ? <Text style={s.outfitOccasion}>{block.occasion}</Text> : null}
            </View>
          </View>
          <View style={s.outfitStrip}>
            {block.items.map((item) => {
              const uri =
                item.image_url && item.image_url.startsWith("/")
                  ? `${media?.baseUrl ?? ""}${item.image_url}`
                  : item.image_url;
              return (
                <View key={item.garment_id} style={s.outfitItem}>
                  {uri ? (
                    <Image source={{ uri, headers: media?.headers }} style={s.outfitThumb} resizeMode="cover" />
                  ) : (
                    <View style={[s.outfitThumb, s.outfitThumbEmpty]} />
                  )}
                  <Text style={s.gridLabel} numberOfLines={1}>
                    {item.label}
                  </Text>
                </View>
              );
            })}
          </View>
          <Text style={s.cardBody}>{block.rationale}</Text>
          <View style={s.outfitActions}>
            <Pressable
              style={s.voteButton}
              onPress={() => onReaction("outfit_liked", block.id, block.agent)}
            >
              <Text style={s.voteText}>Wear it 👍</Text>
            </Pressable>
            <Pressable
              style={[s.voteButton, s.voteButtonMuted]}
              onPress={() => onReaction("outfit_rejected", block.id, block.agent)}
            >
              <Text style={[s.voteText, s.voteTextMuted]}>Not for me</Text>
            </Pressable>
          </View>
        </View>
      );

    case "agent_card": {
      const card = <AgentCardView block={block} />;
      return block.screen ? (
        <Pressable onPress={() => ctx.onNavigate?.(block.screen!)}>{card}</Pressable>
      ) : (
        card
      );
    }

    case "agent_grid":
      return (
        <View style={ag.grid}>
          {block.items.map((item) => (
            <Pressable
              key={item.screen}
              style={ag.cell}
              onPress={() => ctx.onNavigate?.(item.screen)}
            >
              <View style={[ag.tile, ag[`tile_${item.tone ?? "indigo"}` as keyof typeof ag] as object]}>
                <Text style={ag.tileText}>{item.name[0]}</Text>
              </View>
              <Text style={ag.name}>{item.name}</Text>
              <Text style={ag.sub} numberOfLines={2}>{item.sub}</Text>
            </Pressable>
          ))}
        </View>
      );

    case "draft_card":
      return <DraftCardView block={block} onDraftAction={ctx.onDraftAction} dark={dark} />;

    case "timeline":
      return <TimelineView block={block} dark={dark} />;

    case "day_strip":
      return <DayStripView block={block} />;

    case "ring_hero": {
      const R = 34;
      const C = 2 * Math.PI * R;
      const pct = Math.max(0, Math.min(1, block.pct ?? 0));
      return (
        <LinearGradient
          colors={["#2A2050", "#140F26", "#08070E"]}
          start={{ x: 0.2, y: 0 }}
          end={{ x: 0.6, y: 1 }}
          style={cn.heroCard}
        >
          <View style={{ flex: 1 }}>
            <Text style={cn.heroBig}>{block.big}</Text>
            <Text style={cn.heroLabel}>{block.label}</Text>
            <View style={{ flexDirection: "row", gap: 10, marginTop: 12 }}>
              {(block.chips ?? []).map((c, i) => (
                <Text key={i} style={cn.heroChip}>{c}</Text>
              ))}
            </View>
          </View>
          <View style={{ width: 84, height: 84, alignItems: "center", justifyContent: "center" }}>
            <Svg width={84} height={84} viewBox="0 0 84 84">
              <Circle cx={42} cy={42} r={R} stroke="rgba(199,184,255,0.16)" strokeWidth={7} fill="none" />
              <Circle
                cx={42}
                cy={42}
                r={R}
                stroke="#8B7CF6"
                strokeWidth={7}
                fill="none"
                strokeLinecap="round"
                strokeDasharray={`${C * pct} ${C}`}
                transform="rotate(-90 42 42)"
              />
            </Svg>
            <View style={{ position: "absolute", alignItems: "center" }}>
              <Text style={cn.ringPct}>{Math.round(pct * 100)}%</Text>
              {block.pct_label ? <Text style={cn.ringLabel}>{block.pct_label}</Text> : null}
            </View>
          </View>
        </LinearGradient>
      );
    }

    case "bar_chart": {
      const maxVal = Math.max(...block.bars.map((b) => b.value), block.target ?? 0, 1);
      return (
        <View style={[s.card, dark && dk.card, { paddingVertical: 16 }]}>
          <View style={{ flexDirection: "row", alignItems: "flex-end", height: 96, gap: 8 }}>
            {block.bars.map((b, i) => (
              <View key={i} style={{ flex: 1, alignItems: "center" }}>
                <View
                  style={{
                    alignSelf: "stretch",
                    height: Math.max(3, Math.round((b.value / maxVal) * 84)),
                    borderRadius: 3,
                    backgroundColor: b.accent ? "#C7B8FF" : "rgba(199,184,255,0.28)",
                  }}
                />
                <Text style={[mt.label, { marginTop: 6, fontSize: 9 }]}>{b.label.toUpperCase()}</Text>
              </View>
            ))}
          </View>
          {block.caption ? (
            <Text style={[s.caption, dark && dk.caption, { marginTop: 10 }]}>{block.caption}</Text>
          ) : null}
        </View>
      );
    }

    case "meter_row":
      return (
        <View style={[s.card, dark && dk.card, { paddingVertical: 16 }]}>
          {block.meters.map((m, i) => {
            const pct = Math.max(0, Math.min(1, m.max > 0 ? m.value / m.max : 0));
            const left = Math.max(0, Math.round(m.max - m.value));
            return (
              <View key={i} style={{ marginTop: i > 0 ? 14 : 0 }}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 6 }}>
                  <Text style={mt.label}>{m.label}</Text>
                  <Text style={[mt.left, { color: METER_TONES[m.tone ?? "lavender"] }]}>
                    {left}
                    {m.unit ?? "g"} left
                  </Text>
                </View>
                <View style={mt.track}>
                  <View
                    style={[
                      mt.fill,
                      { width: `${Math.round(pct * 100)}%`, backgroundColor: METER_TONES[m.tone ?? "lavender"] },
                    ]}
                  />
                </View>
              </View>
            );
          })}
        </View>
      );

    case "action_row":
      return (
        <View style={s.actionRow}>
          {block.actions.map((action) => (
            <Pressable
              key={action.id}
              style={[
                s.action,
                dark && dk.action,
                action.style === "secondary" && s.actionSecondary,
                dark && action.style === "secondary" && dk.actionSecondary,
                action.style === "destructive" && s.actionDestructive,
              ]}
              onPress={() => onReaction("action_tapped", action.id)}
            >
              <Text
                style={[
                  s.actionText,
                  action.style === "secondary" && s.actionTextSecondary,
                  dark && dk.actionText,
                ]}
              >
                {action.label}
              </Text>
            </Pressable>
          ))}
        </View>
      );

    default:
      return null; // unknown block from a newer server — degrade silently
  }
}

// Nano V1 palette — indigo black, lavender accent, mint for live/positive,
// Instrument Serif display over JetBrains Mono micro-labels.
const tl = StyleSheet.create({
  wrap: {
    borderRadius: 18,
    padding: 14,
    backgroundColor: "#F4F2FA",
  },
  wrapDark: {
    backgroundColor: "#14101F",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
  },
  row: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingVertical: 9,
  },
  rowBorder: {
    borderTopWidth: 1,
    borderTopColor: "rgba(0,0,0,0.06)",
  },
  rowBorderDark: {
    borderTopColor: "rgba(199,184,255,0.08)",
  },
  time: {
    width: 44,
    fontSize: 11,
    color: "#8B87A0",
    fontVariant: ["tabular-nums"],
    paddingTop: 1,
  },
  mid: { flex: 1 },
  text: {
    fontSize: 14,
    color: "#2A2637",
    marginBottom: 3,
  },
  verdict: {
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1.1,
  },
  footer: {
    marginTop: 10,
    fontSize: 12,
    color: "#8B87A0",
    fontStyle: "italic",
  },
});

const dk = StyleSheet.create({
  actionSecondary: {
    backgroundColor: "#14101F",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.18)",
  },
  screenTitle: { color: "#F4F2FA", fontFamily: "InstrumentSerif_400Regular", fontWeight: "400" },
  sectionTitle: {
    color: "#8A87A3",
    fontFamily: "JetBrainsMono_400Regular",
    textTransform: "uppercase",
    fontSize: 10,
    letterSpacing: 1.6,
    fontWeight: "400",
  },
  ink: { color: "#F4F2FA" },
  title: { color: "#F4F2FA", fontFamily: "InstrumentSerif_400Regular", fontWeight: "400" },
  subtitle: { color: "#B9B4CC" },
  body: { color: "#B9B4CC" },
  bodyText: { color: "#B9B4CC" },
  caption: { color: "#8A87A3" },
  mono: {
    color: "#8A87A3",
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 9,
    letterSpacing: 1,
  },
  card: {
    backgroundColor: "#14101F",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    shadowOpacity: 0,
  },
  statCell: { backgroundColor: "#14101F" },
  whyChip: {
    color: "#FF9DA8",
    backgroundColor: "rgba(255,157,168,0.12)",
    fontFamily: "JetBrainsMono_400Regular",
  },
  draftBody: {
    color: "#D6D2E6",
    borderLeftColor: "rgba(199,184,255,0.30)",
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 16,
    lineHeight: 22,
  },
  draftEdit: { color: "#F4F2FA", borderColor: "rgba(199,184,255,0.25)", backgroundColor: "#0E0C18" },
  voteButton: { backgroundColor: "#C7B8FF" },
  voteText: { color: "#14101F" },
  voteButtonMuted: { backgroundColor: "#221C36" },
  action: { backgroundColor: "#C7B8FF" },
  actionText: { color: "#14101F" },
});

const s = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 4 },
  gridCell: {},
  gridImage: { width: "100%", aspectRatio: 0.85, borderRadius: 14, backgroundColor: "#ECEAE6" },
  gridLabel: { fontSize: 11, color: "#8A8781", marginTop: 4 },
  outfitCard: {
    backgroundColor: "#FFFFFF",
    borderRadius: 18,
    padding: 16,
    marginTop: 10,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  outfitHeader: { flexDirection: "row", alignItems: "flex-start" },
  outfitTitle: { fontSize: 17, fontWeight: "700", color: "#1A1916", letterSpacing: -0.3 },
  outfitOccasion: { fontSize: 12, color: "#8A8781", marginTop: 2, textTransform: "capitalize" },
  outfitStrip: { flexDirection: "row", gap: 10, marginVertical: 12 },
  outfitItem: { width: 72 },
  outfitThumb: { width: 72, height: 88, borderRadius: 12, backgroundColor: "#ECEAE6" },
  outfitThumbEmpty: { borderWidth: 1, borderColor: "#E2DFDA", borderStyle: "dashed" },
  outfitActions: { flexDirection: "row", gap: 8, marginTop: 12 },
  voteButton: {
    flex: 1,
    backgroundColor: "#1A1916",
    borderRadius: 12,
    paddingVertical: 10,
    alignItems: "center",
  },
  voteButtonMuted: { backgroundColor: "#F1EFEB" },
  voteText: { color: "#FFF", fontWeight: "600", fontSize: 14 },
  voteTextMuted: { color: "#6E6B65" },
  draftCard: {
    backgroundColor: "#FFFFFF",
    borderRadius: 18,
    padding: 16,
    marginTop: 10,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  draftTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  draftFrom: { fontSize: 16, fontWeight: "700", color: "#1A1916", letterSpacing: -0.2 },
  whyChip: {
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 0.8,
    color: "#8A5A00",
    backgroundColor: "#FBF0DC",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    overflow: "hidden",
  },
  draftSubject: { fontSize: 13, color: "#6E6B65", marginTop: 2 },
  draftLabel: { fontSize: 10, fontWeight: "700", letterSpacing: 1, color: "#B0ACA4", marginTop: 12 },
  draftBody: {
    fontSize: 15,
    lineHeight: 21,
    color: "#3B3934",
    marginTop: 6,
    paddingLeft: 10,
    borderLeftWidth: 2,
    borderLeftColor: "#E4E1DB",
    fontStyle: "italic",
  },
  draftEdit: {
    fontSize: 15,
    lineHeight: 21,
    color: "#1A1916",
    marginTop: 6,
    padding: 10,
    borderWidth: 1,
    borderColor: "#D8D5CF",
    borderRadius: 10,
    minHeight: 80,
    textAlignVertical: "top",
  },
  screenTitle: { fontSize: 32, fontWeight: "700", color: "#1A1A1A", marginBottom: 4 },
  section: { marginTop: 20 },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "600",
    color: "#8A8A85",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 10,
  },
  title: { fontSize: 22, fontWeight: "700", color: "#1A1A1A", marginBottom: 6 },
  subtitle: { fontSize: 17, fontWeight: "600", color: "#3B3B3B", marginBottom: 4 },
  body: { fontSize: 15, color: "#3B3B3B", lineHeight: 22 },
  caption: { fontSize: 13, color: "#8A8A85", marginTop: 2 },
  card: {
    backgroundColor: "#FFFFFF",
    borderRadius: 14,
    padding: 16,
    marginBottom: 12,
    borderLeftWidth: 3,
    borderLeftColor: "#E4E4E0",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  cardTitle: { fontSize: 16, fontWeight: "600", color: "#1A1A1A", flex: 1, marginBottom: 6 },
  cardBody: { fontSize: 14, color: "#5A5A56", lineHeight: 21 },
  dismiss: { fontSize: 14, color: "#B5B5B0", paddingLeft: 12 },
  cardAction: { marginTop: 12, alignSelf: "flex-start" },
  cardActionText: { fontSize: 14, fontWeight: "600", color: "#0A66C2" },
  agentTag: { fontSize: 11, color: "#B5B5B0", marginTop: 10, textTransform: "uppercase", letterSpacing: 0.5 },
  statRow: { flexDirection: "row", gap: 10, marginBottom: 12 },
  stat: {
    flex: 1,
    backgroundColor: "#FFFFFF",
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 12,
    alignItems: "center",
  },
  statValue: { fontSize: 22, fontWeight: "700", color: "#1A1A1A" },
  statUnit: { fontSize: 13, fontWeight: "500", color: "#8A8A85" },
  statDelta: { fontSize: 12, fontWeight: "600", marginTop: 2 },
  statLabel: { fontSize: 12, color: "#8A8A85", marginTop: 4, textAlign: "center" },
  imageCard: { backgroundColor: "#FFFFFF", borderRadius: 14, padding: 12, marginBottom: 12 },
  image: { width: "100%", height: 180, borderRadius: 10, marginBottom: 8 },
  listItem: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#ECECEA",
  },
  trailing: { fontSize: 15, fontWeight: "600", color: "#1A1A1A", marginLeft: 12 },
  actionRow: { flexDirection: "row", gap: 10, marginBottom: 12 },
  action: {
    flex: 1,
    backgroundColor: "#1A1A1A",
    borderRadius: 12,
    paddingVertical: 13,
    alignItems: "center",
  },
  actionSecondary: { backgroundColor: "#EDEDEA" },
  actionDestructive: { backgroundColor: "#B3261E" },
  actionText: { color: "#FFFFFF", fontSize: 15, fontWeight: "600" },
  actionTextSecondary: { color: "#1A1A1A" },
});
