// The orb: Nano, always present, always one tap from listening.
// Idle it breathes at the top of every screen. Tapped, it glides into the
// screen (nothing else moves), listens, and acts — navigation, mail checks,
// the get-to-know-you conversation. Voice is the app's front door.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { useAudioPlayer } from "expo-audio";
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from "expo-speech-recognition";

type OrbPhase = "idle" | "greeting" | "listening" | "thinking" | "speaking";

export function NanoOrb({
  apiUrl,
  auth,
  onNavigate,
  onRefreshInbox,
  onStartInterview,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onNavigate: (screen: string) => void;
  onRefreshInbox: () => void;
  onStartInterview: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<OrbPhase>("idle");
  const [transcript, setTranscript] = useState("");
  const [say, setSay] = useState("");
  const [sayUrl, setSayUrl] = useState<string | null>(null);
  const offeredInterview = useRef(false);
  const finalRef = useRef("");

  const glide = useRef(new Animated.Value(0)).current; // 0 = docked, 1 = in-screen
  const breath = useRef(new Animated.Value(0)).current;
  const dim = useRef(new Animated.Value(0)).current;

  const player = useAudioPlayer(sayUrl ? { uri: sayUrl, headers: auth } : null);
  useEffect(() => {
    if (sayUrl) {
      try {
        player.play();
      } catch {}
    }
  }, [sayUrl]);

  // Breathe always; breathe faster while listening.
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(breath, {
          toValue: 1,
          duration: phase === "listening" ? 700 : 2200,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(breath, {
          toValue: 0,
          duration: phase === "listening" ? 700 : 2200,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [phase === "listening"]);

  useSpeechRecognitionEvent("result", (e) => {
    if (!open) return;
    const best = e.results?.[0]?.transcript ?? "";
    const text = (finalRef.current + " " + best).trim();
    setTranscript(text);
    if (e.isFinal) {
      finalRef.current = text;
      submit(text);
    }
  });
  useSpeechRecognitionEvent("end", () => {
    if (!open) return;
    // Recognition ended on silence with no final result — use what we heard.
    if (phase === "listening" && finalRef.current === "" && transcript.trim()) {
      submit(transcript.trim());
    }
  });
  useSpeechRecognitionEvent("error", () => {
    if (!open) return;
    if (phase === "listening") setPhase("greeting");
  });

  const speak = useCallback((text: string) => {
    setSay(text);
    setSayUrl(`${apiUrl}/v1/voice/speak?text=${encodeURIComponent(text)}`);
  }, [apiUrl]);

  const listen = useCallback(async () => {
    const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!perm.granted) {
      setSay("I need the microphone to hear you.");
      return;
    }
    finalRef.current = "";
    setTranscript("");
    setPhase("listening");
    ExpoSpeechRecognitionModule.start({ lang: "en-US", interimResults: true, continuous: false });
  }, []);

  const collapse = useCallback(() => {
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch {}
    Animated.parallel([
      Animated.timing(glide, { toValue: 0, duration: 380, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(dim, { toValue: 0, duration: 380, useNativeDriver: true }),
    ]).start(() => {
      setOpen(false);
      setPhase("idle");
      setTranscript("");
      setSay("");
      setSayUrl(null);
    });
  }, []);

  const submit = useCallback(
    async (text: string) => {
      try {
        ExpoSpeechRecognitionModule.stop();
      } catch {}
      if (!text) {
        setPhase("greeting");
        return;
      }
      setPhase("thinking");
      try {
        const res = await fetch(`${apiUrl}/v1/voice/command`, {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: JSON.stringify({ transcript: text }),
        });
        const r = await res.json();
        if (r.intent === "start_interview") {
          collapse();
          onStartInterview();
          return;
        }
        if (r.intent === "open_screen" && r.screen) {
          speak(r.say || `Opening ${r.screen}.`);
          setPhase("speaking");
          onNavigate(r.screen);
          setTimeout(collapse, 1400);
          return;
        }
        if (r.intent === "refresh_inbox") {
          speak(r.say || "Checking your mail.");
          setPhase("speaking");
          onRefreshInbox();
          setTimeout(collapse, 1400);
          return;
        }
        // answer / none: say it, then listen again so it feels like a conversation.
        speak(r.say || "Hmm — try that once more?");
        setPhase("speaking");
        setTimeout(() => listen(), Math.min(4000, 900 + (r.say?.length ?? 20) * 55));
      } catch {
        setSay("I couldn't reach the server.");
        setPhase("greeting");
      }
    },
    [apiUrl, auth, collapse, listen, onNavigate, onRefreshInbox, onStartInterview, speak]
  );

  const openOrb = useCallback(async () => {
    setOpen(true);
    Animated.parallel([
      Animated.timing(glide, { toValue: 1, duration: 520, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(dim, { toValue: 1, duration: 520, useNativeDriver: true }),
    ]).start();
    setPhase("greeting");
    try {
      const res = await fetch(`${apiUrl}/v1/voice/hello`, { method: "POST", headers: auth });
      const h = await res.json();
      if (h.offer === "interview" && !offeredInterview.current) {
        offeredInterview.current = true;
        speak(h.say);
        setPhase("speaking");
        setTimeout(() => listen(), Math.min(9000, 1200 + h.say.length * 55));
        return;
      }
    } catch {}
    listen();
  }, [apiUrl, auth, listen, speak]);

  const scale = breath.interpolate({ inputRange: [0, 1], outputRange: [1, phase === "listening" ? 1.16 : 1.06] });
  const orbTranslateY = glide.interpolate({ inputRange: [0, 1], outputRange: [0, 190] });
  const orbScale = glide.interpolate({ inputRange: [0, 1], outputRange: [1, 2.4] });

  const status =
    phase === "listening" ? "LISTENING" : phase === "thinking" ? "THINKING" : phase === "speaking" ? "" : "";

  return (
    <>
      {open ? (
        <Animated.View style={[o.overlay, { opacity: dim }]}>
          <Pressable style={StyleSheet.absoluteFill} onPress={collapse} />
        </Animated.View>
      ) : null}
      <View pointerEvents="box-none" style={[o.dock, { top: insets.top + 4 }]}>
        <Animated.View
          style={{ transform: [{ translateY: orbTranslateY }, { scale: Animated.multiply(orbScale, scale) }] }}
        >
          <Pressable onPress={open ? collapse : openOrb} hitSlop={14}>
            <LinearGradient
              colors={["#C7B8FF", "#6D5BD0", "#2A2050"]}
              start={{ x: 0.2, y: 0.1 }}
              end={{ x: 0.8, y: 1 }}
              style={o.orb}
            />
          </Pressable>
        </Animated.View>
        {open ? (
          <View pointerEvents="box-none" style={o.voicePanel}>
            {status ? <Text style={o.status}>{status}</Text> : null}
            {say && phase !== "listening" ? <Text style={o.say}>{say}</Text> : null}
            {transcript && phase === "listening" ? <Text style={o.transcript}>{transcript}</Text> : null}
            {phase === "listening" ? (
              <Pressable onPress={() => submit(transcript.trim())}>
                <Text style={o.doneHint}>I'M DONE</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}
      </View>
    </>
  );
}

const o = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(8,7,14,0.88)",
    zIndex: 40,
  },
  dock: {
    position: "absolute",
    left: 0,
    right: 0,
    alignItems: "center",
    zIndex: 50,
  },
  orb: {
    width: 38,
    height: 38,
    borderRadius: 19,
    shadowColor: "#C7B8FF",
    shadowOpacity: 0.55,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 0 },
  },
  voicePanel: {
    position: "absolute",
    top: 320,
    left: 32,
    right: 32,
    alignItems: "center",
  },
  status: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 3,
    color: "#7CF7C4",
    marginBottom: 14,
  },
  say: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 24,
    lineHeight: 31,
    color: "#F4F2FA",
    textAlign: "center",
  },
  transcript: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 22,
    lineHeight: 29,
    color: "#C7B8FF",
    textAlign: "center",
  },
  doneHint: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 11,
    letterSpacing: 2.5,
    color: "#8A87A3",
    marginTop: 22,
    padding: 8,
  },
});
