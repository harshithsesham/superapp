import Constants from "expo-constants";
import { LinearGradient } from "expo-linear-gradient";
import { useFonts } from "expo-font";
import { InstrumentSans_400Regular, InstrumentSans_600SemiBold } from "@expo-google-fonts/instrument-sans";
import { InstrumentSerif_400Regular } from "@expo-google-fonts/instrument-serif";
import { JetBrainsMono_400Regular } from "@expo-google-fonts/jetbrains-mono";
import * as ImagePicker from "expo-image-picker";
import * as Notifications from "expo-notifications";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { ConversationProvider } from "@elevenlabs/react-native";
import {
  isHealthDataAvailable,
  queryStatisticsForQuantity,
  requestAuthorization as requestHealthAuthorization,
} from "@kingstinct/react-native-healthkit";
import { InterviewScreen } from "./src/InterviewScreen";
import { NanoOrb } from "./src/NanoOrb";
import { CalScreen } from "./src/CalScreen";
import { FlightsScreen } from "./src/FlightsScreen";
import { HubScreen } from "./src/HubScreen";
import { InboxScreen } from "./src/InboxScreen";
import { ProfileScreen } from "./src/ProfileScreen";
import { SduiScreen } from "./src/sdui/renderer";
import { SDUI_VERSION } from "./src/sdui/types";
import type { Screen } from "./src/sdui/types";

const extra = (Constants.expoConfig?.extra ?? {}) as { apiUrl?: string; apiToken?: string };
// Live module bindings: session bootstrap / sign-in reassign these, and every
// callback reads them at call time.
let apiUrl = extra.apiUrl ?? "";
let AUTH: { Authorization: string } = { Authorization: `Bearer ${extra.apiToken ?? ""}` };
let USER_NAME = "";

type StoredSession = { url: string; token: string; user?: string; name?: string };

function applySession(sess: StoredSession) {
  apiUrl = sess.url;
  AUTH = { Authorization: `Bearer ${sess.token}` };
  USER_NAME = sess.name || sess.user || "";
}

// Show Nano's pushes even when the app is foregrounded — without this, iOS
// silently swallows notifications that arrive while the app is open.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

const SCREENS = ["hub", "inbox", "home", "finance", "stylist", "flights", "profile"] as const;
type ScreenName = (typeof SCREENS)[number];

// A crash must never be a black screen: show the error and offer a retry.
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <View style={{ flex: 1, backgroundColor: "#08070E", justifyContent: "center", padding: 32 }}>
          <Text style={{ color: "#FF9DA8", fontSize: 16, marginBottom: 12 }}>
            Something broke on this screen.
          </Text>
          <Text style={{ color: "#8A87A3", fontSize: 12, marginBottom: 24 }}>
            {String(this.state.error?.message ?? this.state.error)}
          </Text>
          <Pressable
            style={{ backgroundColor: "#C7B8FF", borderRadius: 12, padding: 14, alignItems: "center" }}
            onPress={() => this.setState({ error: null })}
          >
            <Text style={{ color: "#08070E", fontWeight: "600" }}>Try again</Text>
          </Pressable>
        </View>
      );
    }
    return this.props.children;
  }
}

function Boot() {
  // Visible while fonts/session resolve — never a black void.
  return (
    <View style={{ flex: 1, backgroundColor: "#08070E", justifyContent: "center", alignItems: "center" }}>
      <ActivityIndicator color="#C7B8FF" />
    </View>
  );
}

export default function AppRoot() {
  return (
    <ErrorBoundary>
      <ConversationProvider>
        <App />
      </ConversationProvider>
    </ErrorBoundary>
  );
}

function App() {
  const [authState, setAuthState] = useState<"loading" | "signin" | "ready">("loading");
  const [interviewing, setInterviewing] = useState(false);
  const [screenName, setScreenName] = useState<ScreenName>("hub");
  const [screen, setScreen] = useState<Screen | null>(null);
  const screenNameRef = useRef<ScreenName>("hub");
  screenNameRef.current = screenName;
  const screenCache = useRef<Partial<Record<ScreenName, Screen>>>({});
  const [lastTheme, setLastTheme] = useState<"light" | "dark">("dark");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mealText, setMealText] = useState("");
  const [fontsLoaded] = useFonts({
    InstrumentSerif_400Regular,
    InstrumentSans_400Regular,
    InstrumentSans_600SemiBold,
    JetBrainsMono_400Regular,
  });

  const applyScreen = useCallback(async (res: globalThis.Response) => {
    if (!res.ok) throw new Error(`Couldn't reach the API: ${res.status}`);
    const data: Screen = await res.json();
    if ((data.version ?? 1) > SDUI_VERSION) {
      throw new Error(
        `This server speaks SDUI v${data.version}; the app supports v${SDUI_VERSION}. Update the app.`
      );
    }
    screenCache.current[screenNameRef.current] = data;
    setLastTheme(data.theme === "dark" ? "dark" : "light");
    setScreen(data);
  }, []);

  // GET = pure render from the substrate; POST /refresh runs the agent's
  // think step first (that's where cognition and fact writes happen).
  const load = useCallback(
    async (fresh = false) => {
      try {
        setError(null);
        await applyScreen(
          await fetch(`${apiUrl}/v1/screen/${screenName}${fresh ? "/refresh" : ""}`, {
            method: fresh ? "POST" : "GET",
            headers: AUTH,
          })
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setRefreshing(false);
      }
    },
    [applyScreen, screenName]
  );

  // Session bootstrap: stored sign-in > build-time dev token > sign-in screen.
  useEffect(() => {
    (async () => {
      try {
        const raw = await SecureStore.getItemAsync("session");
        if (raw) {
          applySession(JSON.parse(raw) as StoredSession);
          setAuthState("ready");
          return;
        }
      } catch {}
      setAuthState(extra.apiToken ? "ready" : "signin");
    })();
  }, []);

  useEffect(() => {
    if (authState !== "ready") return;
    // Show the cached screen instantly (no flash), refresh silently behind it.
    const cached = screenCache.current[screenName] ?? null;
    setScreen(cached);
    if (cached) setLastTheme(cached.theme === "dark" ? "dark" : "light");
    load();
  }, [load, authState, screenName]);

  const [healthSignal, setHealthSignal] = useState(0);

  // HealthKit: read today's steps + active energy, report to the server.
  // Best-effort — denied permission or no data just means no activity line.
  useEffect(() => {
    if (authState !== "ready") return;
    (async () => {
      try {
        if (!isHealthDataAvailable()) return;
        void healthSignal;
        const ok = await requestHealthAuthorization({
          toRead: [
            "HKQuantityTypeIdentifierStepCount",
            "HKQuantityTypeIdentifierActiveEnergyBurned",
          ],
        });
        if (!ok) return;
        const start = new Date();
        start.setHours(0, 0, 0, 0);
        const filter = { date: { startDate: start, endDate: new Date() } };
        const steps = await queryStatisticsForQuantity(
          "HKQuantityTypeIdentifierStepCount",
          ["cumulativeSum"],
          { filter, unit: "count" }
        );
        const energy = await queryStatisticsForQuantity(
          "HKQuantityTypeIdentifierActiveEnergyBurned",
          ["cumulativeSum"],
          { filter, unit: "kcal" }
        );
        await fetch(`${apiUrl}/v1/nutrition/activity`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body: JSON.stringify({
            steps: Math.round(steps.sumQuantity?.quantity ?? 0),
            active_kcal: Math.round(energy.sumQuantity?.quantity ?? 0),
          }),
        });
      } catch {
        // no HealthKit in this environment; fine
      }
    })();
  }, [authState, healthSignal]);

  // Push registration — direct APNs (no Expo services): the raw device token
  // goes to our server, which signs its own pushes with the .p8 key.
  useEffect(() => {
    if (authState !== "ready") return;
    (async () => {
      try {
        const perm = await Notifications.requestPermissionsAsync();
        if (!perm.granted) return;
        const device = await Notifications.getDevicePushTokenAsync();
        const token =
          typeof device.data === "string" ? device.data : JSON.stringify(device.data);
        await fetch(`${apiUrl}/v1/devices/push-token`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body: JSON.stringify({ token, kind: "apns" }),
        });
      } catch {
        // no push in this environment (simulator, denied permission); fine
      }
    })();
  }, [authState]);

  const logMealText = useCallback(async () => {
    const description = mealText.trim();
    if (!description || busy) return;
    setBusy(true);
    try {
      setError(null);
      await applyScreen(
        await fetch(`${apiUrl}/v1/nutrition/log`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body: JSON.stringify({ description }),
        })
      );
      setMealText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [mealText, busy, applyScreen]);

  const uploadPhoto = useCallback(async (endpoint: string) => {
    if (busy) return;
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    const result = perm.granted
      ? await ImagePicker.launchCameraAsync({ quality: 0.6 })
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.6 });
    if (result.canceled || !result.assets.length) return;

    const asset = result.assets[0];
    const body = new FormData();
    body.append("photo", {
      uri: asset.uri,
      name: asset.fileName ?? "meal.jpg",
      type: asset.mimeType ?? "image/jpeg",
    } as unknown as Blob);

    setBusy(true);
    try {
      setError(null);
      await applyScreen(
        await fetch(`${apiUrl}${endpoint}`, { method: "POST", headers: AUTH, body })
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [busy, applyScreen]);

  const signOut = useCallback(async () => {
    try {
      await SecureStore.deleteItemAsync("session");
    } catch {}
    apiUrl = extra.apiUrl ?? "";
    AUTH = { Authorization: "Bearer " };
    setScreen(null);
    setScreenName("hub");
    setAuthState("signin");
  }, []);

  const [orbSignal, setOrbSignal] = useState(0);
  const [stageSignal, setStageSignal] = useState(0);

  const onFixMeal = useCallback((mealId: string) => {
    Alert.prompt(
      "Fix this estimate",
      "What's wrong? e.g. 'it was mutton, not veg' or 'half the portion'",
      async (note) => {
        if (!note?.trim()) return;
        setBusy(true);
        try {
          setError(null);
          await applyScreen(
            await fetch(`${apiUrl}/v1/nutrition/meals/${mealId}/fix`, {
              method: "POST",
              headers: { ...AUTH, "Content-Type": "application/json" },
              body: JSON.stringify({ note: note.trim() }),
            })
          );
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        } finally {
          setBusy(false);
        }
      }
    );
  }, [applyScreen]);

  const onNavigate = useCallback((screen: string) => {
    if ((SCREENS as readonly string[]).includes(screen)) {
      setScreenName(screen as ScreenName);
    }
  }, []);

  const draftInFlight = useRef<Set<string>>(new Set());
  const onDraftAction = useCallback(
    async (action: "send" | "defer" | "save" | "now", draftId: string, body?: string) => {
      if (draftInFlight.current.has(draftId)) return; // first tap is still working
      draftInFlight.current.add(draftId);
      try {
        setError(null);
        if (action === "save") {
          await fetch(`${apiUrl}/v1/inbox/drafts/${draftId}`, {
            method: "PUT",
            headers: { ...AUTH, "Content-Type": "application/json" },
            body: JSON.stringify({ body }),
          });
          return;
        }
        const res = await fetch(`${apiUrl}/v1/inbox/drafts/${draftId}/${action}`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body:
            action === "defer"
              ? JSON.stringify({ tz_offset_minutes: new Date().getTimezoneOffset() })
              : undefined,
        });
        if (res.status === 403) {
          setError("Sending is off until you enable it (gmail_scope_tier=send).");
          return;
        }
        if (res.status === 409) {
          // Already sent — an earlier tap won. Not an error; show the fresh truth.
          await load();
          return;
        }
        await applyScreen(res);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        draftInFlight.current.delete(draftId);
      }
    },
    [applyScreen, load]
  );

  const onReaction = useCallback(
    (kind: string, targetId: string, agent?: string) => {
      // Client-handled actions (SDUI buttons that trigger flows, not just logs).
      if (kind === "action_tapped" && targetId === "interview.start") {
        setInterviewing(true);
        return;
      }
      if (kind === "action_tapped" && targetId === "inbox.connect") {
        (async () => {
          setBusy(true);
          try {
            setError(null);
            const res = await fetch(`${apiUrl}/v1/gmail/auth-url`, { headers: AUTH });
            if (res.ok) {
              // Real Gmail: consent in an in-app browser, bounced back via superapp://
              const { auth_url } = await res.json();
              await WebBrowser.openAuthSessionAsync(auth_url, "superapp://gmail-connected");
              await load();
            } else {
              // Server in stub-mailbox mode: demo connect
              await applyScreen(
                await fetch(`${apiUrl}/v1/inbox/connect/stub`, { method: "POST", headers: AUTH })
              );
            }
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        })();
        return;
      }
      if (kind === "action_tapped" && targetId.startsWith("kernel.promote:")) {
        // The explicit yes that grants autonomy — never taken, only given.
        const actionKey = targetId.slice("kernel.promote:".length);
        setBusy(true);
        fetch(`${apiUrl}/v1/kernel/promote`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body: JSON.stringify({ action_key: actionKey }),
        })
          .then(async (res) => {
            if (!res.ok) {
              const d = await res.json().catch(() => null);
              setError(d?.detail ?? "That one is not earned yet.");
            }
            await load();
          })
          .catch((e) => setError(e instanceof Error ? e.message : String(e)))
          .finally(() => setBusy(false));
        return;
      }
      if (kind === "action_tapped" && targetId === "nutrition.setup") {
        setOrbSignal((n) => n + 1); // summon Nano; it asks, you answer
        return;
      }
      if (kind === "action_tapped" && targetId === "nutrition.photo") {
        uploadPhoto("/v1/nutrition/photo");
        return;
      }
      if (kind === "action_tapped" && targetId === "nutrition.water") {
        setBusy(true);
        fetch(`${apiUrl}/v1/nutrition/water`, {
          method: "POST",
          headers: { ...AUTH, "Content-Type": "application/json" },
          body: JSON.stringify({ ml: 250 }),
        })
          .then((res) => applyScreen(res))
          .catch((e) => setError(e instanceof Error ? e.message : String(e)))
          .finally(() => setBusy(false));
        return;
      }
      if (kind === "action_tapped" && targetId === "finance.link") {
        (async () => {
          setBusy(true);
          try {
            setError(null);
            const res = await fetch(`${apiUrl}/v1/finance/link/hosted`, {
              method: "POST",
              headers: AUTH,
            });
            if (res.ok) {
              // Real banks: Plaid's hosted flow in the browser, bounced back
              // via superapp:// — the webhook completes the link server-side.
              const { hosted_link_url } = await res.json();
              await WebBrowser.openAuthSessionAsync(hosted_link_url, "superapp://bank-linked");
              await load(true);
              setTimeout(() => load(), 8000); // webhook exchange can trail the bounce
            } else {
              // Plaid not configured: the stub bank keeps the vertical usable.
              await applyScreen(
                await fetch(`${apiUrl}/v1/finance/link/sandbox`, { method: "POST", headers: AUTH })
              );
            }
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        })();
        return;
      }
      fetch(`${apiUrl}/v1/reactions`, {
        method: "POST",
        headers: { ...AUTH, "Content-Type": "application/json" },
        body: JSON.stringify({ kind, target_id: targetId, agent }),
      }).catch(() => {});
    },
    [applyScreen]
  );

  const dark = (screen?.theme ?? lastTheme) === "dark";
  if (!fontsLoaded || authState === "loading") return <Boot />;
  if (interviewing) {
    return (
      <InterviewScreen
        apiUrl={apiUrl}
        auth={AUTH}
        onDone={() => {
          setInterviewing(false);
          setScreen(null);
          load();
        }}
      />
    );
  }
  if (authState === "signin") {
    return (
      <SignInScreen
        defaultUrl={extra.apiUrl ?? ""}
        onSignedIn={(sess) => {
          applySession(sess);
          setAuthState("ready");
        }}
      />
    );
  }
  return (
    <SafeAreaProvider>
    <SafeAreaView style={[styles.root, dark && styles.rootDark]} edges={["top", "left", "right"]}>
      <StatusBar style={dark ? "light" : "auto"} />
      {screenName !== "hub" ? (
        <View style={styles.tabs}>
          <Pressable
            style={[styles.tab, dark && styles.tabDark]}
            onPress={() => setScreenName("hub")}
          >
            <Text style={[styles.backText, dark && styles.backTextDark]}>‹  MY HUB</Text>
          </Pressable>
        </View>
      ) : (
        <View style={styles.tabs} />
      )}
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            tintColor={dark ? "#C7B8FF" : undefined}
            titleColor={dark ? "#8A87A3" : "#6E6B65"}
            title={screenName === "inbox" ? "Checking your mail…" : "Refreshing…"}
            onRefresh={async () => {
              setRefreshing(true);
              const wasInbox = screenName === "inbox";
              await load(true);
              if (wasInbox) {
                // Triage continues server-side; quietly pick up what it finds.
                setTimeout(() => load(), 5000);
                setTimeout(() => load(), 12000);
              }
            }}
          />
        }
      >
        {error ? (
          <Text style={styles.error}>{error}</Text>
        ) : screenName === "home" || screenName === "flights" || screenName === "inbox" || screenName === "profile" ? null : screen ? (
          screenName === "hub" ? (
            <HubScreen
              screen={screen}
              onNavigate={onNavigate}
              onReaction={onReaction}
            />
          ) : (
            <SduiScreen
              screen={screen}
              onReaction={onReaction}
              media={{ baseUrl: apiUrl, headers: AUTH }}
              onDraftAction={onDraftAction}
              onNavigate={onNavigate}
              onFix={onFixMeal}
            />
          )
        ) : (
          <ActivityIndicator style={{ marginTop: 64 }} />
        )}
      </ScrollView>

      {screenName === "inbox" && !error ? (
        <View style={{ position: "absolute", top: 60, left: 0, right: 0, bottom: 0 }}>
          <InboxScreen apiUrl={apiUrl} auth={AUTH} onBack={() => setScreenName("hub")} />
        </View>
      ) : null}

      {screenName === "profile" && !error ? (
        <View style={{ position: "absolute", top: 60, left: 0, right: 0, bottom: 0 }}>
          <ProfileScreen
            apiUrl={apiUrl}
            auth={AUTH}
            userName={USER_NAME || (screen?.title?.match(/, (.+)\.$/)?.[1] ?? "")}
            onSignOut={signOut}
          />
        </View>
      ) : null}

      {screenName === "flights" && !error ? (
        <View style={{ position: "absolute", top: 60, left: 0, right: 0, bottom: 0 }}>
          <FlightsScreen
            apiUrl={apiUrl}
            auth={AUTH}
            onBack={() => setScreenName("hub")}
            onOpenOrb={() => setOrbSignal((n) => n + 1)}
          />
        </View>
      ) : null}

      {screenName === "home" && !error ? (
        <View style={{ position: "absolute", top: 60, left: 0, right: 0, bottom: 0 }}>
          <CalScreen
            apiUrl={apiUrl}
            auth={AUTH}
            onOpenOrb={() => setOrbSignal((n) => n + 1)}
            onConnectHealth={() => setHealthSignal((n) => n + 1)}
            onBack={() => setScreenName("hub")}
          />
        </View>
      ) : null}

      {false && (
      <View style={[styles.logBar, dark && styles.logBarDark]}>
        <TextInput
          style={[styles.input, dark && styles.inputDark]}
          placeholder="What did you eat?"
          placeholderTextColor={dark ? "#8A87A3" : "#9A9A97"}
          value={mealText}
          onChangeText={setMealText}
          onSubmitEditing={logMealText}
          returnKeyType="send"
          editable={!busy}
        />
        <Pressable style={styles.logButton} onPress={logMealText} disabled={busy}>
          <Text style={styles.logButtonText}>Log</Text>
        </Pressable>
        <Pressable
          style={styles.photoButton}
          onPress={() => uploadPhoto("/v1/nutrition/photo")}
          disabled={busy}
        >
          <Text style={styles.logButtonText}>{busy ? "…" : "📷"}</Text>
        </Pressable>
      </View>
      )}
      {screenName === "stylist" && (
        <View style={[styles.logBar, dark && styles.logBarDark]}>
          <Pressable
            style={[styles.logButton, { flex: 1 }]}
            onPress={() => uploadPhoto("/v1/wardrobe/photo")}
            disabled={busy}
          >
            <Text style={styles.logButtonText}>{busy ? "…" : "📷  Add garment"}</Text>
          </Pressable>
        </View>
      )}

      {screenName === "hub" || screenName === "profile" || screenName === "inbox" ? (
        <View style={styles.dockBar}>
          <Pressable style={styles.dockSide} onPress={() => setScreenName("hub")} hitSlop={10}>
            <Text style={[styles.dockLabel, screenName === "hub" && styles.dockActive]}>HUB</Text>
          </Pressable>
          <Pressable onPress={() => setStageSignal((n) => n + 1)} hitSlop={12} style={styles.dockBallWrap}>
            <LinearGradient colors={["#C7B8FF", "#6D5BD0", "#2A2050"]}
                            start={{ x: 0.2, y: 0.1 }} end={{ x: 0.8, y: 1 }}
                            style={styles.dockBall} />
          </Pressable>
          <Pressable style={styles.dockSide} onPress={() => setScreenName("profile")} hitSlop={10}>
            <Text style={[styles.dockLabel, screenName === "profile" && styles.dockActive]}>PROFILE</Text>
          </Pressable>
        </View>
      ) : null}

      <NanoOrb
        apiUrl={apiUrl}
        auth={AUTH}
        openSignal={orbSignal}
        stageSignal={stageSignal}
        onNavigate={onNavigate}
        onRefreshInbox={() => {
          setScreenName("inbox");
          load(true);
          setTimeout(() => load(), 6000);
        }}
        onActed={() => load()}
      />
    </SafeAreaView>
    </SafeAreaProvider>
  );
}

function SignInScreen({
  defaultUrl,
  onSignedIn,
}: {
  defaultUrl: string;
  onSignedIn: (sess: StoredSession) => void;
}) {
  const [url, setUrl] = useState(defaultUrl);
  const [showUrl, setShowUrl] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const signIn = useCallback(async () => {
    const server = url.trim().replace(/\/$/, "");
    if (!server || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await WebBrowser.openAuthSessionAsync(
        `${server}/v1/auth/google/start`,
        "superapp://signed-in"
      );
      if (res.type !== "success" || !res.url) {
        if (res.type === "cancel" || res.type === "dismiss") return;
        throw new Error("Sign-in did not complete");
      }
      const params: Record<string, string> = {};
      for (const pair of (res.url.split("?")[1] ?? "").split("&")) {
        const [k, v] = pair.split("=");
        if (k) params[k] = decodeURIComponent(v ?? "");
      }
      if (!params.token) throw new Error("No session returned");
      const sess: StoredSession = {
        url: server,
        token: params.token,
        user: params.user,
        name: params.name,
      };
      await SecureStore.setItemAsync("session", JSON.stringify(sess));
      onSignedIn(sess);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [url, busy, onSignedIn]);

  return (
    <SafeAreaProvider>
      <SafeAreaView style={si.root}>
        <View style={si.body}>
          <Text style={si.kicker}>YOUR CHIEF OF STAFF</Text>
          <Text style={si.title}>I run the boring{"\n"}half of your life.</Text>
          <Text style={si.sub}>
            One agent per vertical — inbox, meals, money, wardrobe — on one shared memory.
          </Text>
          {error ? <Text style={si.error}>{error}</Text> : null}
          <Pressable style={si.googleBtn} onPress={signIn} disabled={busy}>
            <Text style={si.googleText}>{busy ? "Signing in…" : "Continue with Google"}</Text>
          </Pressable>
          <Pressable onPress={() => setShowUrl((v) => !v)}>
            <Text style={si.advanced}>{showUrl ? "Hide server" : "Choose server"}</Text>
          </Pressable>
          {showUrl ? (
            <TextInput
              style={si.urlInput}
              value={url}
              onChangeText={setUrl}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="https://app.example.com"
              placeholderTextColor="#55524C"
            />
          ) : null}
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const si = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#08070E" },
  body: { flex: 1, justifyContent: "center", padding: 28, gap: 14 },
  kicker: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    letterSpacing: 2,
    color: "#8A87A3",
  },
  title: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 40,
    lineHeight: 46,
    color: "#F4F2FA",
  },
  sub: {
    fontFamily: "InstrumentSans_400Regular",
    fontSize: 15,
    lineHeight: 22,
    color: "#B9B4CC",
    marginBottom: 14,
  },
  error: { color: "#FF9DA8", fontSize: 13 },
  googleBtn: {
    backgroundColor: "#F4F2FA",
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: "center",
  },
  googleText: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 16, color: "#14101F" },
  advanced: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    letterSpacing: 1,
    color: "#8A87A3",
    textAlign: "center",
    marginTop: 6,
  },
  urlInput: {
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.25)",
    borderRadius: 12,
    padding: 12,
    color: "#F4F2FA",
    fontSize: 14,
    fontFamily: "JetBrainsMono_400Regular",
  },
});

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F7F7F5" },
  rootDark: { backgroundColor: "#08070E" },
  dockBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    flexDirection: "row", alignItems: "center",
    paddingBottom: 28, paddingTop: 12, paddingHorizontal: 36,
    backgroundColor: "rgba(6,5,12,0.97)",
    borderTopWidth: 1, borderTopColor: "rgba(199,184,255,0.12)",
    zIndex: 40,
  },
  dockSide: { flex: 1, alignItems: "center" },
  dockLabel: {
    fontFamily: "JetBrainsMono_400Regular", fontSize: 10,
    letterSpacing: 3, color: "#8A87A3",
  },
  dockActive: { color: "#C7B8FF" },
  dockBallWrap: { marginTop: -26 },
  dockBall: {
    width: 56, height: 56, borderRadius: 28,
    shadowColor: "#9F8CFF", shadowOpacity: 0.9, shadowRadius: 18,
    shadowOffset: { width: 0, height: 0 },
    borderWidth: 1, borderColor: "rgba(199,184,255,0.4)",
  },
  tabs: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingTop: 8 },
  tab: { paddingVertical: 6, paddingHorizontal: 14, borderRadius: 16, backgroundColor: "#ECECEA" },
  tabActive: { backgroundColor: "#1A1A1A" },
  tabDark: { backgroundColor: "#14101F" },
  tabActiveDark: { backgroundColor: "#C7B8FF" },
  tabText: { fontSize: 13, fontWeight: "600", color: "#3B3B3B" },
  backText: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    letterSpacing: 1.2,
    color: "#3B3B3B",
  },
  backTextDark: { color: "#C7B8FF" },
  tabTextActive: { color: "#FFF" },
  tabTextDark: { color: "#8A87A3" },
  tabTextActiveDark: { color: "#14101F" },
  logBarDark: { backgroundColor: "#0B0A14", borderTopColor: "rgba(199,184,255,0.12)" },
  logButtonDark: { backgroundColor: "#C7B8FF" },
  logButtonTextDark: { color: "#14101F" },
  scroll: { padding: 16, paddingBottom: 48 },
  error: { color: "#B3261E", marginTop: 48, textAlign: "center" },
  logBar: {
    flexDirection: "row",
    gap: 8,
    padding: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#DDD",
    backgroundColor: "#FFFFFF",
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#DDD",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 15,
    color: "#1A1A1A",
  },
  logButton: {
    backgroundColor: "#1A1A1A",
    borderRadius: 10,
    paddingHorizontal: 16,
    justifyContent: "center",
  },
  photoButton: {
    backgroundColor: "#3B3B3B",
    borderRadius: 10,
    paddingHorizontal: 12,
    justifyContent: "center",
  },
  logButtonText: { color: "#FFF", fontWeight: "600", fontSize: 15 },
  inputDark: {
    borderColor: "rgba(199,184,255,0.22)",
    backgroundColor: "#14101F",
    color: "#F4F2FA",
  },
});
