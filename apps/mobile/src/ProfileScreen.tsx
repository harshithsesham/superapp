// Profile: who you are to Nano — your mailboxes (link as many as you like),
// what Nano auto-replies to, and everything it has come to know (people and
// what it's learned about you). A page, matched to Nano V1 (8).
import { LinearGradient } from "expo-linear-gradient";
import Constants from "expo-constants";
import * as Application from "expo-application";
import * as WebBrowser from "expo-web-browser";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Modal, Pressable, ScrollView, StyleSheet, Text, View,
} from "react-native";

const C = {
  bg: "#04040A", panel: "rgba(25,18,51,0.5)", border: "rgba(199,184,255,0.14)",
  text: "#F4F2FA", muted: "#8A87A3", body: "#C9C5DA", lav: "#C7B8FF",
  mint: "#7CF7C4", rose: "#FF9DA8",
};
const MONO = "JetBrainsMono_400Regular";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";

type Mailbox = {
  email: string; primary: boolean; color: string; count: number; provider?: string;
};
type Provider = { provider: string; label: string; hint: string };
type PersonRow = { name: string; email: string; relationship: string; summary: string };
type FactRow = { domain: string; key: string; belief: string };
type Facet = { name: string; n: number };
type Knows = {
  facets: Facet[]; people: PersonRow[]; facts: FactRow[];
};

function initials(s: string): string {
  const t = (s || "?").trim();
  return t.slice(0, 1).toUpperCase();
}

export function ProfileScreen({
  apiUrl, auth, userName, onSignOut, onChanged,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  userName: string;
  onSignOut: () => void;
  onChanged?: () => void;
}) {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [reauth, setReauth] = useState(false);
  const [autoKinds, setAutoKinds] = useState<string[]>([]);
  const [autoSenders, setAutoSenders] = useState<string[]>([]);
  const [knows, setKnows] = useState<Knows | null>(null);
  const [facet, setFacet] = useState("People");
  const [linking, setLinking] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [choosing, setChoosing] = useState(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [iRes, kRes, pRes] = await Promise.all([
        fetch(`${apiUrl}/v1/inbox/state`, { headers: auth }),
        fetch(`${apiUrl}/v1/profile/knows`, { headers: auth }),
        fetch(`${apiUrl}/v1/mail/providers`, { headers: auth }),
      ]);
      if (!alive.current) return;
      if (iRes.ok) {
        const d = await iRes.json();
        setMailboxes(d.mailboxes ?? []);
        setReauth(!!d.reauth?.needed);
        setAutoKinds(d.auto_reply_kinds ?? []);
        setAutoSenders(d.auto_reply_senders ?? []);
      }
      if (kRes.ok) setKnows(await kRes.json());
      if (pRes.ok) setProviders((await pRes.json()).providers ?? []);
    } catch { /* quiet */ }
  }, [apiUrl, auth]);

  useEffect(() => {
    alive.current = true;
    refresh();
    return () => { alive.current = false; };
  }, [refresh]);

  const linkMailbox = useCallback(async (provider = "gmail") => {
    if (linking) return;
    setChoosing(false);
    setLinking(true);
    try {
      // Each provider owns its consent URL; the callback deep link is shared,
      // so the app comes back the same way whichever one the person picked.
      const res = await fetch(`${apiUrl}/v1/${provider}/auth-url`, { headers: auth });
      if (res.ok) {
        const { auth_url } = await res.json();
        await WebBrowser.openAuthSessionAsync(auth_url, "superapp://gmail-connected");
      } else {
        await fetch(`${apiUrl}/v1/inbox/connect/stub`, { method: "POST", headers: auth });
      }
      await refresh();
      onChanged?.();
    } catch { /* quiet */ } finally {
      if (alive.current) setLinking(false);
    }
  }, [apiUrl, auth, linking, refresh, onChanged]);

  const stopAuto = useCallback(async (body: { kind?: string; sender?: string }) => {
    setAutoKinds((k) => k.filter((x) => x !== body.kind));
    setAutoSenders((k) => k.filter((x) => x !== body.sender));
    try {
      await fetch(`${apiUrl}/v1/inbox/autoreply`, {
        method: "DELETE", headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      onChanged?.();
    } catch { /* refresh reconciles */ }
  }, [apiUrl, auth, onChanged]);

  const autoOn = autoKinds.length + autoSenders.length;
  const rows: { name: string; sub: string }[] =
    facet === "People"
      ? (knows?.people ?? []).map((p) => ({
          name: p.name || p.email,
          sub: p.relationship || p.summary || "still learning who this is",
        }))
      : (knows?.facts ?? []).map((f) => ({ name: f.key.replace(/_/g, " "), sub: f.belief }));

  return (
    <ScrollView style={{ flex: 1, backgroundColor: C.bg }} contentContainerStyle={s.scroll}>
      <View style={s.headRow}>
        <LinearGradient colors={["#C7B8FF", "#6D5BD0", "#2A2050"]}
                        start={{ x: 0.2, y: 0.1 }} end={{ x: 0.8, y: 1 }} style={s.avatar}>
          <Text style={s.avatarText}>{initials(userName)}</Text>
        </LinearGradient>
        <View style={{ flex: 1 }}>
          <Text style={s.name}>{userName || "You"}</Text>
          <Text style={s.sub}>
            {mailboxes.length
              ? `${mailboxes.length} MAILBOX${mailboxes.length === 1 ? "" : "ES"} · NANO'S PERSON`
              : "NANO KNOWS YOU AS ITS PERSON"}
          </Text>
        </View>
      </View>

      {/* Mailboxes */}
      <View style={s.sectionRow}>
        <Text style={s.sectionTitle}>Mailboxes</Text>
        <Text style={s.count}>{mailboxes.length || "none"}</Text>
      </View>
      <Text style={s.sectionSub}>Link as many as you like. Each just needs a sign-in.</Text>
      <View style={s.panel}>
        {mailboxes.map((m, i) => (
          <View key={m.email} style={[s.mailRow, i > 0 && s.divider]}>
            <View style={[s.dot, { backgroundColor: m.color }]} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Text style={s.mailAddr} numberOfLines={1}>{m.email}</Text>
                {m.primary ? <Text style={s.primaryBadge}>PRIMARY</Text> : null}
              </View>
              <Text style={s.mailMeta}>
                {m.provider === "outlook" ? "Outlook" : m.provider === "stub" ? "Offline" : "Gmail"}
                {" · "}{m.count} synced{reauth && m.primary ? " · reconnect needed" : ""}
              </Text>
            </View>
            {reauth && m.primary ? (
              <Pressable onPress={() => linkMailbox(m.provider || "gmail")} hitSlop={8}>
                <Text style={[s.chip, { color: C.rose }]}>RECONNECT</Text>
              </Pressable>
            ) : (
              <Text style={[s.chip, { color: C.mint }]}>LIVE</Text>
            )}
          </View>
        ))}
        <Pressable style={[s.linkRow, mailboxes.length > 0 && s.divider]} disabled={linking}
                   onPress={() => (providers.length > 1
                     ? setChoosing(true)
                     : linkMailbox(providers[0]?.provider ?? "gmail"))}>
          <View style={s.plus}><Text style={{ color: C.lav, fontSize: 18, marginTop: -2 }}>+</Text></View>
          <Text style={s.linkText}>{linking ? "Opening sign-in…" : "Link another mailbox"}</Text>
        </Pressable>
        <Text style={s.footnote}>
          You sign in with the provider. I never see the password, and I only read the
          mailbox you grant.
        </Text>
      </View>

      {/* Auto-reply */}
      <View style={s.sectionRow}>
        <Text style={s.sectionTitle}>Auto-reply</Text>
        <Text style={[s.count, { color: autoOn ? C.mint : C.muted }]}>{autoOn || "off"}</Text>
      </View>
      <Text style={s.sectionSub}>
        {autoOn
          ? "Kinds and people I answer without asking. Every one is signed as you and lands under Worth knowing."
          : "Nothing is auto-replied. Everything comes to you as a draft instead."}
      </Text>
      {autoOn ? (
        <View style={s.panel}>
          {autoKinds.map((k, i) => (
            <View key={"k" + k} style={[s.autoRow, i > 0 && s.divider]}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={s.autoName}>{k}</Text>
                <Text style={s.autoMeta}>this kind of email</Text>
              </View>
              <Pressable onPress={() => stopAuto({ kind: k })} hitSlop={8}>
                <Text style={[s.chip, { color: C.rose }]}>STOP</Text>
              </Pressable>
            </View>
          ))}
          {autoSenders.map((a, i) => (
            <View key={"s" + a} style={[s.autoRow, (autoKinds.length + i) > 0 && s.divider]}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={s.autoName} numberOfLines={1}>{a}</Text>
                <Text style={s.autoMeta}>everything from this sender</Text>
              </View>
              <Pressable onPress={() => stopAuto({ sender: a })} hitSlop={8}>
                <Text style={[s.chip, { color: C.rose }]}>STOP</Text>
              </Pressable>
            </View>
          ))}
        </View>
      ) : null}

      {/* What Nano knows */}
      <View style={s.sectionRow}>
        <Text style={s.sectionTitle}>What Nano knows</Text>
        <Text style={s.count}>
          {(knows?.people.length ?? 0) + (knows?.facts.length ?? 0)}
        </Text>
      </View>
      <Text style={s.sectionSub}>The people it has learned, and what it knows about you.</Text>
      <View style={s.facetRow}>
        {(knows?.facets ?? []).map((f) => (
          <Pressable key={f.name} onPress={() => setFacet(f.name)}
                     style={[s.facet, facet === f.name && s.facetOn]}>
            <Text style={[s.facetText, facet === f.name && { color: C.lav }]}>
              {f.name} <Text style={s.facetN}>{f.n}</Text>
            </Text>
          </Pressable>
        ))}
      </View>
      <View style={s.panel}>
        {rows.length ? rows.map((r, i) => (
          <View key={r.name + i} style={[s.person, i > 0 && s.divider]}>
            <Text style={s.personName}>{r.name}</Text>
            <Text style={s.personRel}>{r.sub}</Text>
          </View>
        )) : (
          <Text style={s.footnote}>
            {facet === "People"
              ? "No people yet. Profiles form from your mail, in and out."
              : "Nothing learned yet. This fills in as you use Nano."}
          </Text>
        )}
      </View>

      <Modal visible={choosing} transparent animationType="slide"
             onRequestClose={() => setChoosing(false)}>
        <View style={s.sheetWrap}>
          <View style={s.sheet}>
            <Text style={s.sheetTitle}>Which mailbox?</Text>
            <Text style={s.sheetSub}>
              You sign in with the provider. Nano never sees the password, and only reads the
              mailbox you grant it.
            </Text>
            {providers.map((pv) => (
              <Pressable key={pv.provider} style={s.providerRow}
                         onPress={() => linkMailbox(pv.provider)}>
                <View style={[s.providerMark,
                              pv.provider === "outlook" && { backgroundColor: "rgba(0,120,212,0.18)",
                                                             borderColor: "rgba(0,120,212,0.5)" }]}>
                  <Text style={[s.providerMarkText,
                                pv.provider === "outlook" && { color: "#5BA8F5" }]}>
                    {pv.label.slice(0, 1)}
                  </Text>
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.providerName}>{pv.label}</Text>
                  <Text style={s.providerHint}>{pv.hint}</Text>
                </View>
                <Text style={s.providerGo}>{"›"}</Text>
              </Pressable>
            ))}
            <Pressable style={s.ghost} onPress={() => setChoosing(false)}>
              <Text style={s.ghostText}>Not now</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      <Pressable style={s.signOut} onPress={onSignOut}>
        <Text style={s.signOutText}>Sign out</Text>
      </Pressable>

      <Text style={s.version}>
        Nano {Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? ""}
        {Application.nativeBuildVersion ? ` (${Application.nativeBuildVersion})` : ""}
      </Text>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  scroll: { padding: 20, paddingBottom: 150 },
  headRow: { flexDirection: "row", alignItems: "center", gap: 16, marginTop: 8 },
  avatar: { width: 64, height: 64, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  avatarText: { fontFamily: SERIF, fontSize: 30, color: "#FFFFFF" },
  name: { fontFamily: SERIF, fontSize: 32, color: C.text },
  sub: { fontFamily: MONO, fontSize: 9, letterSpacing: 2, color: C.muted, marginTop: 4 },
  sectionRow: {
    flexDirection: "row", alignItems: "baseline",
    justifyContent: "space-between", marginTop: 30,
  },
  sectionTitle: { fontFamily: SERIF, fontSize: 24, color: C.text },
  count: { fontFamily: MONO, fontSize: 12, letterSpacing: 1, color: C.muted },
  sectionSub: { fontFamily: SANS, fontSize: 13, lineHeight: 19, color: C.muted, marginTop: 6 },
  panel: {
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    backgroundColor: C.panel, padding: 6, marginTop: 12,
  },
  mailRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  dot: { width: 12, height: 12, borderRadius: 6 },
  mailAddr: { fontFamily: SANS_SEMI, fontSize: 14.5, color: C.text, flexShrink: 1 },
  primaryBadge: {
    fontFamily: MONO, fontSize: 8, letterSpacing: 1, color: C.lav,
    borderWidth: 1, borderColor: "rgba(199,184,255,0.3)", borderRadius: 6,
    paddingHorizontal: 5, paddingVertical: 2,
  },
  mailMeta: { fontFamily: SANS, fontSize: 12, color: C.muted, marginTop: 3 },
  chip: { fontFamily: MONO, fontSize: 9.5, letterSpacing: 1 },
  divider: { borderTopWidth: 1, borderTopColor: "rgba(199,184,255,0.08)" },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  plus: {
    width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(199,184,255,0.1)", borderWidth: 1, borderColor: "rgba(199,184,255,0.22)",
  },
  linkText: { fontFamily: SANS_SEMI, fontSize: 14, color: C.lav },
  footnote: { fontFamily: SANS, fontSize: 12, lineHeight: 17, color: C.muted, padding: 12, paddingTop: 8 },
  autoRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  autoName: { fontFamily: SANS_SEMI, fontSize: 13.5, color: C.text },
  autoMeta: { fontFamily: SANS, fontSize: 11.5, color: C.muted, marginTop: 2 },
  facetRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  facet: {
    paddingHorizontal: 13, paddingVertical: 7, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.04)", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  facetOn: { backgroundColor: "rgba(199,184,255,0.14)", borderColor: "rgba(199,184,255,0.3)" },
  facetText: { fontFamily: SANS, fontSize: 12.5, color: C.body },
  facetN: { fontFamily: MONO, fontSize: 11, color: C.muted },
  person: { padding: 12 },
  personName: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  personRel: { fontFamily: SANS, fontSize: 13, lineHeight: 18, color: C.body, marginTop: 2 },
  signOut: {
    marginTop: 36, borderRadius: 999, borderWidth: 1,
    borderColor: "rgba(255,157,168,0.4)", paddingVertical: 13, alignItems: "center",
  },
  signOutText: { fontFamily: SANS_SEMI, fontSize: 14, color: C.rose },
  providerRow: {
    flexDirection: "row", alignItems: "center", gap: 14, padding: 14, borderRadius: 16,
    backgroundColor: "rgba(25,18,51,0.6)", borderWidth: 1, borderColor: "rgba(199,184,255,0.16)",
  },
  providerMark: {
    width: 40, height: 40, borderRadius: 14, alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(199,184,255,0.14)", borderWidth: 1, borderColor: "rgba(199,184,255,0.32)",
  },
  providerMarkText: { fontFamily: SERIF, fontSize: 20, color: C.lav },
  providerName: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  providerHint: { fontFamily: SANS, fontSize: 12, color: C.muted, marginTop: 2 },
  providerGo: { fontFamily: SANS, fontSize: 20, color: C.muted },
  sheetWrap: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(2,2,6,0.72)" },
  sheet: {
    backgroundColor: "#0B0A16", borderTopLeftRadius: 26, borderTopRightRadius: 26,
    padding: 22, paddingBottom: 38, gap: 12,
    borderWidth: 1, borderColor: "rgba(199,184,255,0.16)",
  },
  sheetTitle: { fontFamily: SERIF, fontSize: 26, color: C.text },
  sheetSub: { fontFamily: SANS, fontSize: 12.5, lineHeight: 18, color: C.muted },
  ghost: {
    flex: 1, paddingVertical: 14, borderRadius: 16, alignItems: "center",
    borderWidth: 1, borderColor: "rgba(199,184,255,0.2)",
  },
  ghostText: { fontFamily: SANS_SEMI, fontSize: 14, color: C.body },
  version: { fontFamily: MONO, fontSize: 10, letterSpacing: 1, color: "rgba(138,135,163,0.5)", textAlign: "center", marginTop: 20 },
});
