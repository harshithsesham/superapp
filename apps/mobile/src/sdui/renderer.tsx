// The thin renderer (architecture §3): agents choose WHICH components and WHAT
// content; how they look was decided once, here. Adding a component to the
// registry = adding a case to this switch + a type in types.ts + blocks.py.
import React from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import type { LeafBlock, Screen, Section } from "./types";

type ReactionFn = (kind: string, targetId: string, agent?: string) => void;

export function SduiScreen({ screen, onReaction }: { screen: Screen; onReaction: ReactionFn }) {
  return (
    <View>
      <Text style={s.screenTitle}>{screen.title}</Text>
      {screen.sections.map((section, i) => (
        <SduiSection key={i} section={section} onReaction={onReaction} />
      ))}
    </View>
  );
}

function SduiSection({ section, onReaction }: { section: Section; onReaction: ReactionFn }) {
  return (
    <View style={s.section}>
      {section.title ? <Text style={s.sectionTitle}>{section.title}</Text> : null}
      {section.blocks.map((block, i) => (
        <Block key={i} block={block} onReaction={onReaction} />
      ))}
    </View>
  );
}

function Block({ block, onReaction }: { block: LeafBlock; onReaction: ReactionFn }) {
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
      return <Text style={style}>{block.text}</Text>;
    }

    case "insight_card": {
      const accent =
        block.emphasis === "positive" ? "#1E7F4F" : block.emphasis === "warning" ? "#B3261E" : "#3B3B3B";
      return (
        <View style={[s.card, { borderLeftColor: accent }]}>
          <View style={s.cardHeader}>
            <Text style={s.cardTitle}>{block.title}</Text>
            <Pressable
              hitSlop={12}
              onPress={() => onReaction("insight_dismissed", block.id, block.agent)}
            >
              <Text style={s.dismiss}>✕</Text>
            </Pressable>
          </View>
          <Text style={s.cardBody}>{block.body}</Text>
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
        <View style={s.statRow}>
          {block.stats.map((stat, i) => (
            <View key={i} style={s.stat}>
              <Text style={s.statValue}>
                {stat.value}
                {stat.unit ? <Text style={s.statUnit}> {stat.unit}</Text> : null}
              </Text>
              {stat.delta ? (
                <Text style={[s.statDelta, { color: stat.delta.startsWith("-") ? "#B3261E" : "#1E7F4F" }]}>
                  {stat.delta}
                </Text>
              ) : null}
              <Text style={s.statLabel}>{stat.label}</Text>
            </View>
          ))}
        </View>
      );

    case "image_card":
      return (
        <View style={s.imageCard}>
          <Image source={{ uri: block.image_url }} style={s.image} resizeMode="cover" />
          {block.title ? <Text style={s.cardTitle}>{block.title}</Text> : null}
          {block.subtitle ? <Text style={s.caption}>{block.subtitle}</Text> : null}
        </View>
      );

    case "list":
      return (
        <View style={s.card}>
          {block.items.map((item) => (
            <View key={item.id} style={s.listItem}>
              <View style={{ flex: 1 }}>
                <Text style={s.body}>{item.title}</Text>
                {item.subtitle ? <Text style={s.caption}>{item.subtitle}</Text> : null}
              </View>
              {item.trailing ? <Text style={s.trailing}>{item.trailing}</Text> : null}
            </View>
          ))}
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
                action.style === "secondary" && s.actionSecondary,
                action.style === "destructive" && s.actionDestructive,
              ]}
              onPress={() => onReaction("action_tapped", action.id)}
            >
              <Text style={[s.actionText, action.style === "secondary" && s.actionTextSecondary]}>
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

const s = StyleSheet.create({
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
