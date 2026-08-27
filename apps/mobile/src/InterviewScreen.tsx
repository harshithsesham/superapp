// The identity interview — meeting your chief of staff. Nano asks (voice via
// server TTS), you answer by talking (on-device speech recognition).
import { useAudioPlayer } from "expo-audio";
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from "expo-speech-recognition";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

type Turn = {
  session_id: string;
  question: string;
  audio_url: string | null;
  done: boolean;
  progress: number;
  section: string;
};

export function InterviewScreen({
  apiUrl,
  auth,
  onDone,
}: {
  apiUrl: string;
  auth: { Authorization: string };
  onDone: () => void;
}) {
  const [turn, setTurn] = useState<Turn | null>(null);
  const [phase, setPhase] = useState<"loading" | "asking" | "listening" | "thinking" | "finished">(
    "loading"
  );
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const finalRef = useRef("");

  const audioSource = turn?.audio_url
    ? { uri: `${apiUrl}${turn.audio_url}`, headers: auth }
    : null;
  const player = useAudioPlayer(audioSource);

  useEffect(() => {
    if (audioSource) {
      try {
        player.play();
      } catch {}
    }
  }, [turn?.audio_url]);

  useSpeechRecognitionEvent("result", (e) => {
    const best = e.results?.[0]?.transcript ?? "";
    if (e.isFinal) {
      finalRef.current = (finalRef.current + " " + best).trim();
      setTranscript(finalRef.current);
    } else {
      setTranscript((finalRef.current + " " + best).trim());
    }
  });
  useSpeechRecognitionEvent("error", () => {
    // Recognition hiccup: keep whatever we have; user can retry or type nothing.
    setPhase((p) => (p === "listening" ? "asking" : p));
  });

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await fetch(`${apiUrl}/v1/interview/start`, { method: "POST", headers: auth });
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data: Turn = await res.json();
      setTurn(data);
      setPhase(data.done ? "finished" : "asking");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const startListening = useCallback(async () => {
    const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!perm.granted) {
      setError("Microphone permission is needed — or type your answer later in settings.");
      return;
    }
    finalRef.current = "";
    setTranscript("");
    setPhase("listening");
    ExpoSpeechRecognitionModule.start({
      lang: "en-US",
      interimResults: true,
      continuous: true,
    });
  }, []);

  const finishAnswer = useCallback(async () => {
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch {}
    const text = transcript.trim();
    if (!text || !turn) {
      setPhase("asking");
      return;
    }
    setPhase("thinking");
    try {
      const res = await fetch(`${apiUrl}/v1/interview/${turn.session_id}/answer`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data: Turn = await res.json();
      setTurn(data);
      setTranscript("");
      finalRef.current = "";
      setPhase(data.done ? "finished" : "asking");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("asking");
    }
  }, [transcript, turn]);

  const progressPct = Math.round((turn?.progress ?? 0) * 100);

  return (
    <SafeAreaProvider>
      <SafeAreaView style={s.root} edges={["top", "left", "right", "bottom"]}>
        <View style={s.header}>
          <Text style={s.kicker}>MEETING YOUR CHIEF OF STAFF</Text>
          <View style={s.progressTrack}>
            <View style={[s.progressFill, { width: `${Math.max(progressPct, 3)}%` }]} />
          </View>
          <Pressable onPress={onDone} hitSlop={12}>
            <Text style={s.pause}>PAUSE</Text>
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={s.body}>
          {error ? <Text style={s.error}>{error}</Text> : null}
          {!turn ? (
            <ActivityIndicator color="#C7B8FF" style={{ marginTop: 80 }} />
          ) : (
            <>
              <Text style={s.question}>{turn.question}</Text>
              {phase === "listening" ? (
                <Text style={s.transcript}>{transcript || "…listening"}</Text>
              ) : null}
              {phase === "thinking" ? (
                <Text style={s.status}>NANO IS CONSIDERING…</Text>
              ) : null}
            </>
          )}
        </ScrollView>

        <View style={s.controls}>
          {phase === "asking" && (
            <Pressable style={s.talkBtn} onPress={startListening}>
              <Text style={s.talkText}>Hold on — let me answer</Text>
            </Pressable>
          )}
          {phase === "listening" && (
            <Pressable style={[s.talkBtn, s.doneBtn]} onPress={finishAnswer}>
              <Text style={s.talkText}>I'm done</Text>
            </Pressable>
          )}
          {phase === "finished" && (
            <Pressable style={s.talkBtn} onPress={onDone}>
              <Text style={s.talkText}>Open my Hub</Text>
            </Pressable>
          )}
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#08070E" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 20,
    paddingTop: 10,
  },
  kicker: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 9,
    letterSpacing: 1.5,
    color: "#8A87A3",
  },
  progressTrack: {
    flex: 1,
    height: 3,
    borderRadius: 2,
    backgroundColor: "#221C36",
    overflow: "hidden",
  },
  progressFill: { height: 3, backgroundColor: "#C7B8FF" },
  pause: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 1.2,
    color: "#8A87A3",
  },
  body: { padding: 24, paddingTop: 48, gap: 22 },
  question: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 30,
    lineHeight: 38,
    color: "#F4F2FA",
  },
  transcript: {
    fontFamily: "InstrumentSans_400Regular",
    fontSize: 16,
    lineHeight: 23,
    color: "#B9B4CC",
    borderLeftWidth: 2,
    borderLeftColor: "rgba(199,184,255,0.35)",
    paddingLeft: 12,
  },
  status: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 1.5,
    color: "#7CF7C4",
  },
  error: { color: "#FF9DA8", fontSize: 13 },
  controls: { padding: 20 },
  talkBtn: {
    backgroundColor: "#C7B8FF",
    borderRadius: 16,
    paddingVertical: 16,
    alignItems: "center",
  },
  doneBtn: { backgroundColor: "#7CF7C4" },
  talkText: { fontFamily: "InstrumentSans_600SemiBold", fontSize: 16, color: "#14101F" },
});
