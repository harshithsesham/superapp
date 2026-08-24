// Mirrors apps/api/superapp/sdui/blocks.py — the single source of truth.
// Regenerate reference: packages/sdui-schema/schema.json

export type TextBlock = {
  type: "text";
  text: string;
  variant?: "body" | "title" | "subtitle" | "caption";
};

export type InsightCard = {
  type: "insight_card";
  id: string;
  agent: string;
  title: string;
  body: string;
  emphasis?: "default" | "positive" | "warning";
  action_label?: string | null;
  action_id?: string | null;
};

export type Stat = { label: string; value: string; delta?: string | null; unit?: string | null };
export type StatRow = { type: "stat_row"; stats: Stat[] };

export type ImageCard = {
  type: "image_card";
  image_url: string;
  title?: string | null;
  subtitle?: string | null;
};

export type ListItem = { id: string; title: string; subtitle?: string | null; trailing?: string | null };
export type ListBlock = { type: "list"; items: ListItem[] };

export type Action = { id: string; label: string; style?: "primary" | "secondary" | "destructive" };
export type ActionRow = { type: "action_row"; actions: Action[] };

export type LeafBlock = TextBlock | InsightCard | StatRow | ImageCard | ListBlock | ActionRow;

export type Section = { type: "section"; title?: string | null; blocks: LeafBlock[] };
export type Screen = { type: "screen"; title: string; sections: Section[] };
