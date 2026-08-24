import Constants from "expo-constants";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
} from "react-native";
import { SduiScreen } from "./src/sdui/renderer";
import type { Screen } from "./src/sdui/types";

const { apiUrl, apiToken } = (Constants.expoConfig?.extra ?? {}) as {
  apiUrl: string;
  apiToken: string;
};

export default function App() {
  const [screen, setScreen] = useState<Screen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await fetch(`${apiUrl}/v1/screen/home`, {
        headers: { Authorization: `Bearer ${apiToken}` },
      });
      if (!res.ok) throw new Error(`API ${res.status}`);
      setScreen(await res.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onReaction = useCallback(
    (kind: string, targetId: string, agent?: string) => {
      fetch(`${apiUrl}/v1/reactions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ kind, target_id: targetId, agent }),
      }).catch(() => {});
    },
    []
  );

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
              load();
            }}
          />
        }
      >
        {error ? (
          <Text style={styles.error}>Couldn't reach the API: {error}</Text>
        ) : screen ? (
          <SduiScreen screen={screen} onReaction={onReaction} />
        ) : (
          <ActivityIndicator style={{ marginTop: 64 }} />
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F7F7F5" },
  scroll: { padding: 16, paddingBottom: 48 },
  error: { color: "#B3261E", marginTop: 48, textAlign: "center" },
});
