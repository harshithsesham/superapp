// The orb: Nano, always present, always one tap from a conversation.
// Idle it breathes at the top of every screen. Tapped, it glides into the
// screen — the app stays visible behind a light veil — and the conversation
// simply runs: Nano speaks, listens, acts, listens again. It ends only when
// you close it, say goodbye, or go quiet for a while. The get-to-know-you
// interview happens right here in the same conversation — no screen switch.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { useAudioPlayer } from "expo-audio";
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from "expo-speech-recognition";

type OrbPhase = "idle" | "listening" | "thinking" | "speaking";
type OrbMode = "converse" | "interview";
type ConverseTurn = { role: "user" | "nano"; text: string };

const MAX_EMPTY_LISTENS = 4; // ~2 quiet minutes before Nano bows out

export function NanoOrb({
  apiUrl,
  auth,
  onNavigate,
  onRefreshInbox,
  onActed,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onNavigate: (screen: string) => void;
  onRefreshInbox: () => void;
  onActed?: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<OrbPhase>("idle");
  const [transcript, setTranscript] = useState("");
  const [say, setSay] = useState("");
  const [sayUrl, setSayUrl] = useState<string | null>(null);
  const [progress, setProgress] = useState(0); // interview progress, 0 hides the bar

  const mode = useRef<OrbMode>("converse");
  const interviewSession = useRef<string | null>(null);
  const history = useRef<ConverseTurn[]>([]);
  const finalRef = useRef("");
  const emptyListens = useRef(0);
  const openRef = useRef(false);
  const speakTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const glide = useRef(new Animated.Value(0)).current;
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

  const listen = useCallback(async () => {
    if (!openRef.current) return;
    const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!perm.granted) {
      setSay("I need the microphone to hear you.");
      return;
    }
    finalRef.current = "";
    setTranscript("");
    setSay("");
    setSayUrl(null);
    setPhase("listening");
    ExpoSpeechRecognitionModule.start({ lang: "en-US", interimResults: true, continuous: false });
  }, []);

  const collapse = useCallback(() => {
    openRef.current = false;
    if (speakTimer.current) clearTimeout(speakTimer.current);
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
      setProgress(0);
      mode.current = "converse";
      interviewSession.current = null;
    });
  }, []);

  // Speak, then either keep the conversation going (listen) or wind down.
  const speakThen = useCallback(
    (text: string, audioUrl: string | null, next: "listen" | "collapse") => {
      setSay(text);
      setSayUrl(audioUrl ?? `${apiUrl}/v1/voice/speak?text=${encodeURIComponent(text)}`);
      setPhase("speaking");
      if (speakTimer.current) clearTimeout(speakTimer.current);
      const ms = Math.min(45000, 1200 + text.length * 58);
      speakTimer.current = setTimeout(() => {
        if (next === "listen") listen();
        else collapse();
      }, ms);
    },
    [apiUrl, collapse, listen]
  );

  const startInterviewInOrb = useCallback(async () => {
    mode.current = "interview";
    try {
      const res = await fetch(`${apiUrl}/v1/interview/start`, { method: "POST", headers: auth });
      const t = await res.json();
      interviewSession.current = t.session_id;
      setProgress(Math.max(t.progress ?? 0, 0.03));
      speakThen(t.question, t.audio_url ? `${apiUrl}${t.audio_url}` : null, "listen");
    } catch {
      mode.current = "converse";
      speakThen("I couldn't start just now — ask me again in a moment.", null, "listen");
    }
  }, [apiUrl, auth, speakThen]);

  const submit = useCallback(
    async (text: string) => {
      try {
        ExpoSpeechRecognitionModule.stop();
      } catch {}
      if (!text) return;
      emptyListens.current = 0;
      setPhase("thinking");

      // Interview mode: same ball, same conversation — answers flow to the
      // interviewer, the next question comes back in Nano's voice.
      if (mode.current === "interview" && interviewSession.current) {
        try {
          const res = await fetch(
            `${apiUrl}/v1/interview/${interviewSession.current}/answer`,
            {
              method: "POST",
              headers: { ...auth, "Content-Type": "application/json" },
              body: JSON.stringify({ text }),
            }
          );
          const t = await res.json();
          setProgress(Math.max(t.progress ?? 0, 0.03));
          if (t.done) {
            mode.current = "converse";
            interviewSession.current = null;
            setProgress(0);
            onActed?.();
          }
          speakThen(t.question, t.audio_url ? `${apiUrl}${t.audio_url}` : null,
            t.done ? "collapse" : "listen");
        } catch {
          speakThen("I lost the thread for a second — say that again?", null, "listen");
        }
        return;
      }

      history.current = [...history.current, { role: "user" as const, text }].slice(-24);
      try {
        const res = await fetch(`${apiUrl}/v1/voice/converse`, {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: JSON.stringify({ messages: history.current }),
        });
        const r = await res.json();
        if (r.say) history.current = [...history.current, { role: "nano" as const, text: r.say }];

        if (r.action === "start_interview") {
          startInterviewInOrb();
          return;
        }
        if (r.action === "open_screen" && r.screen) onNavigate(r.screen);
        if (r.action === "refresh_inbox") onRefreshInbox();
        if (r.acted) onActed?.();

        // Conversation is the default state: keep listening unless Nano is
        // explicitly signing off.
        speakThen(r.say || "Say that once more?", null,
          r.action === "end_conversation" ? "collapse" : "listen");
      } catch {
        speakThen("I couldn't reach the server — one more time?", null, "listen");
      }
    },
    [apiUrl, auth, onActed, onNavigate, onRefreshInbox, speakThen, startInterviewInOrb]
  );

  useSpeechRecognitionEvent("result", (e) => {
    if (!openRef.current) return;
    const best = e.results?.[0]?.transcript ?? "";
    const text = (finalRef.current + " " + best).trim();
    setTranscript(text);
    if (e.isFinal) {
      finalRef.current = text;
      submit(text);
    }
  });
  useSpeechRecognitionEvent("end", () => {
    if (!openRef.current || phase !== "listening") return;
    const heard = transcript.trim();
    if (heard && finalRef.current === "") {
      submit(heard);
    } else if (!heard) {
      // Silence: quietly listen again, up to a point — then bow out.
      emptyListens.current += 1;
      if (emptyListens.current >= MAX_EMPTY_LISTENS) collapse();
      else listen();
    }
  });
  useSpeechRecognitionEvent("error", () => {
    if (!openRef.current) return;
    if (phase === "listening") {
      emptyListens.current += 1;
      if (emptyListens.current >= MAX_EMPTY_LISTENS) collapse();
      else setTimeout(() => listen(), 600);
    }
  });

  const openOrb = useCallback(async () => {
    openRef.current = true;
    emptyListens.current = 0;
    history.current = [];
    setOpen(true);
    Animated.parallel([
      Animated.timing(glide, { toValue: 1, duration: 520, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(dim, { toValue: 1, duration: 520, useNativeDriver: true }),
    ]).start();
    try {
      const res = await fetch(`${apiUrl}/v1/voice/hello`, { method: "POST", headers: auth });
      const h = await res.json();
      if (h.offer === "interview") {
        history.current = [{ role: "nano" as const, text: h.say }];
        speakThen(h.say, null, "listen");
        return;
      }
    } catch {}
    listen();
  }, [apiUrl, auth, listen, speakThen]);

  // Tap while it talks = interrupt: stop the audio, start listening.
  const onOrbTap = useCallback(() => {
    if (!openRef.current) {
      openOrb();
      return;
    }
    if (phase === "speaking") {
      try {
        player.pause();
      } catch {}
      if (speakTimer.current) clearTimeout(speakTimer.current);
      listen();
    } else {
      collapse();
    }
  }, [phase, openOrb, listen, collapse, player]);

  const scale = breath.interpolate({ inputRange: [0, 1], outputRange: [1, phase === "listening" ? 1.16 : 1.06] });
  const orbTranslateY = glide.interpolate({ inputRange: [0, 1], outputRange: [0, 190] });
  const orbScale = glide.interpolate({ inputRange: [0, 1], outputRange: [1, 2.4] });

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
          <Pressable onPress={onOrbTap} hitSlop={14}>
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
            {progress > 0 ? (
              <View style={o.progressTrack}>
                <View style={[o.progressFill, { width: `${Math.round(progress * 100)}%` }]} />
              </View>
            ) : null}
            {phase === "listening" ? (
              <Text style={o.transcript}>{transcript || " "}</Text>
            ) : say ? (
              <Text style={o.say}>{say}</Text>
            ) : null}
            {phase === "thinking" ? <Text style={o.status}>·  ·  ·</Text> : null}
          </View>
        ) : null}
      </View>
    </>
  );
}

const o = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(8,7,14,0.45)",
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
  progressTrack: {
    alignSelf: "stretch",
    height: 2,
    borderRadius: 1,
    backgroundColor: "rgba(199,184,255,0.18)",
    marginBottom: 18,
    overflow: "hidden",
  },
  progressFill: { height: 2, backgroundColor: "#C7B8FF" },
  status: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 14,
    letterSpacing: 4,
    color: "#8A87A3",
    marginTop: 8,
  },
  say: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 23,
    lineHeight: 30,
    color: "#F4F2FA",
    textAlign: "center",
    textShadowColor: "rgba(8,7,14,0.9)",
    textShadowRadius: 10,
  },
  transcript: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 21,
    lineHeight: 28,
    color: "#C7B8FF",
    textAlign: "center",
    textShadowColor: "rgba(8,7,14,0.9)",
    textShadowRadius: 10,
  },
});
