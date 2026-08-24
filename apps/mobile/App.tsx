import Constants from "expo-constants";
import * as ImagePicker from "expo-image-picker";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SduiScreen } from "./src/sdui/renderer";
import { SDUI_VERSION } from "./src/sdui/types";
import type { Screen } from "./src/sdui/types";

const { apiUrl, apiToken } = (Constants.expoConfig?.extra ?? {}) as {
  apiUrl: string;
  apiToken: string;
};
const AUTH = { Authorization: `Bearer ${apiToken}` };

export default function App() {
  const [screen, setScreen] = useState<Screen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mealText, setMealText] = useState("");

  const applyScreen = useCallback(async (res: globalThis.Response) => {
    if (!res.ok) throw new Error(`Couldn't reach the API: ${res.status}`);
    const data: Screen = await res.json();
    if ((data.version ?? 1) > SDUI_VERSION) {
      throw new Error(
        `This server speaks SDUI v${data.version}; the app supports v${SDUI_VERSION}. Update the app.`
      );
    }
    setScreen(data);
  }, []);

  // GET = pure render from the substrate; POST /refresh runs the agent's
  // think step first (that's where cognition and fact writes happen).
  const load = useCallback(
    async (fresh = false) => {
      try {
        setError(null);
        await applyScreen(
          await fetch(`${apiUrl}/v1/screen/home${fresh ? "/refresh" : ""}`, {
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
    [applyScreen]
  );

  useEffect(() => {
    load();
  }, [load]);

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

  const logMealPhoto = useCallback(async () => {
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
        await fetch(`${apiUrl}/v1/nutrition/photo`, { method: "POST", headers: AUTH, body })
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [busy, applyScreen]);

  const onReaction = useCallback((kind: string, targetId: string, agent?: string) => {
    fetch(`${apiUrl}/v1/reactions`, {
      method: "POST",
      headers: { ...AUTH, "Content-Type": "application/json" },
      body: JSON.stringify({ kind, target_id: targetId, agent }),
    }).catch(() => {});
  }, []);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="auto" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              load(true);
            }}
          />
        }
      >
        {error ? (
          <Text style={styles.error}>{error}</Text>
        ) : screen ? (
          <SduiScreen
            screen={screen}
            onReaction={onReaction}
            media={{ baseUrl: apiUrl, headers: AUTH }}
          />
        ) : (
          <ActivityIndicator style={{ marginTop: 64 }} />
        )}
      </ScrollView>

      <View style={styles.logBar}>
        <TextInput
          style={styles.input}
          placeholder="What did you eat?"
          placeholderTextColor="#9A9A97"
          value={mealText}
          onChangeText={setMealText}
          onSubmitEditing={logMealText}
          returnKeyType="send"
          editable={!busy}
        />
        <Pressable style={styles.logButton} onPress={logMealText} disabled={busy}>
          <Text style={styles.logButtonText}>Log</Text>
        </Pressable>
        <Pressable style={styles.photoButton} onPress={logMealPhoto} disabled={busy}>
          <Text style={styles.logButtonText}>{busy ? "…" : "📷"}</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F7F7F5" },
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
});
