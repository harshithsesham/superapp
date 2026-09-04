// Profile: who you are to Nano — identity, the mail connection, the people
// it has come to know — and the door out (sign out lives here now).
import { LinearGradient } from "expo-linear-gradient";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

const C = {
  bg: "#04040A", panel: "rgba(25,18,51,0.5)", border: "rgba(199,184,255,0.14)",
  text: "#F4F2FA", muted: "#8A87A3", body: "#C9C5DA", lav: "#C7B8FF",
  mint: "#7CF7C4", rose: "#FF9DA8",
};
const MONO = "Menlo";
const SERIF = "InstrumentSerif_400Regular";
const SANS = "InstrumentSans_400Regular";
const SANS_SEMI = "InstrumentSans_600SemiBold";

type PersonRow = { email: string; name: string; relationship: string; summary: string };

export function ProfileScreen({
  apiUrl,
  auth,
  userName,
  onSignOut,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  userName: string;
  onSignOut: () => void;
}) {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [mail, setMail] = useState<{ connected: boolean; reauth: boolean } | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    (async () => {
      try {
        const [pRes, iRes] = await Promise.all([
          fetch(`${apiUrl}/v1/people`, { headers: auth }),
          fetch(`${apiUrl}/v1/inbox/state`, { headers: auth }),
        ]);
        if (!alive.current) return;
        if (pRes.ok) setPeople(((await pRes.json()).people ?? []).slice(0, 8));
        if (iRes.ok) {
          const d = await iRes.json();
          setMail({ connected: !!d.connected, reauth: !!d.reauth?.needed });
        }
      } catch { /* quiet */ }
    })();
    return () => { alive.current = false; };
  }, [apiUrl, auth]);

  return (
    <ScrollView style={{ flex: 1, backgroundColor: C.bg }} contentContainerStyle={s.scroll}>
      <View style={s.headRow}>
        <LinearGradient colors={["#C7B8FF", "#6D5BD0", "#2A2050"]}
                        start={{ x: 0.2, y: 0.1 }} end={{ x: 0.8, y: 1 }} style={s.avatar}>
          <Text style={s.avatarText}>
            {(userName || "?").trim().slice(0, 1).toUpperCase()}
          </Text>
        </LinearGradient>
        <View style={{ flex: 1 }}>
          <Text style={s.name}>{userName || "You"}</Text>
          <Text style={s.sub}>NANO KNOWS YOU AS ITS PERSON</Text>
        </View>
      </View>

      <View style={s.panel}>
        <View style={s.row}>
          <Text style={s.rowTitle}>Gmail</Text>
          <Text style={[s.status, {
            color: mail == null ? C.muted : mail.reauth ? C.rose : mail.connected ? C.mint : C.muted,
          }]}>
            {mail == null ? "…" : mail.reauth ? "RECONNECT NEEDED" : mail.connected ? "CONNECTED" : "NOT CONNECTED"}
          </Text>
        </View>
        <Text style={s.rowSub}>
          {mail?.reauth
            ? "Open the inbox and tap Reconnect."
            : "Synced live. Triage, drafts, and the people graph feed from it."}
        </Text>
      </View>

      {people.length ? (
        <>
          <Text style={s.sectionTitle}>People Nano knows</Text>
          <View style={s.panel}>
            {people.map((p, i) => (
              <View key={p.email} style={[s.person, i > 0 && s.divider]}>
                <Text style={s.personName}>{p.name || p.email}</Text>
                {p.relationship ? (
                  <Text style={s.personRel}>{p.relationship}</Text>
                ) : (
                  <Text style={[s.personRel, { color: C.muted }]}>still learning who this is</Text>
                )}
              </View>
            ))}
            <Text style={s.footnote}>
              Profiles sharpen with every email in or out. Services never become people.
            </Text>
          </View>
        </>
      ) : null}

      <Pressable style={s.signOut} onPress={onSignOut}>
        <Text style={s.signOutText}>Sign out</Text>
      </Pressable>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  scroll: { padding: 20, paddingBottom: 140 },
  headRow: { flexDirection: "row", alignItems: "center", gap: 16, marginTop: 8 },
  avatar: { width: 64, height: 64, borderRadius: 22, alignItems: "center", justifyContent: "center" },
  avatarText: { fontFamily: SERIF, fontSize: 30, color: "#FFFFFF" },
  name: { fontFamily: SERIF, fontSize: 32, color: C.text },
  sub: { fontFamily: MONO, fontSize: 9, letterSpacing: 2, color: C.muted, marginTop: 4 },
  panel: {
    borderRadius: 20, borderWidth: 1, borderColor: C.border,
    backgroundColor: C.panel, padding: 16, marginTop: 20,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  rowTitle: { fontFamily: SANS_SEMI, fontSize: 16, color: C.text },
  status: { fontFamily: MONO, fontSize: 10, letterSpacing: 2 },
  rowSub: { fontFamily: SANS, fontSize: 13, lineHeight: 19, color: C.muted, marginTop: 8 },
  sectionTitle: { fontFamily: SERIF, fontSize: 24, color: C.text, marginTop: 30 },
  person: { paddingVertical: 10 },
  divider: { borderTopWidth: 1, borderTopColor: "rgba(199,184,255,0.08)" },
  personName: { fontFamily: SANS_SEMI, fontSize: 15, color: C.text },
  personRel: { fontFamily: SANS, fontSize: 13, color: C.body, marginTop: 2 },
  footnote: { fontFamily: SANS, fontSize: 12, lineHeight: 17, color: C.muted, marginTop: 12 },
  signOut: {
    marginTop: 36, borderRadius: 999, borderWidth: 1,
    borderColor: "rgba(255,157,168,0.4)", paddingVertical: 13, alignItems: "center",
  },
  signOutText: { fontFamily: SANS_SEMI, fontSize: 14, color: C.rose },
});
