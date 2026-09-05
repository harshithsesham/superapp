// The orb: Nano, hanging around the edge of every screen — half-tucked and
// breathing when idle, and when tapped it comes to you: slides out, grows a
// little, and listens. No takeover, no veil — the app stays fully usable;
// the conversation lives in a small bubble beside the orb. It leaves only
// when you tap it again or say goodbye. The get-to-know-you interview
// happens right here in the same conversation — no screen switch.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Animated, Easing, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { setAudioModeAsync, useAudioPlayer } from "expo-audio";
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from "expo-speech-recognition";
import { useConversation } from "@elevenlabs/react-native";

type OrbPhase = "idle" | "listening" | "thinking" | "speaking";
type OrbMode = "converse" | "interview";
type ConverseTurn = { role: "user" | "nano"; text: string };

// Long-press on any inbox card brings Nano to it with that item as the
// subject — the dock opens in chat mode, contextualized. This is what the
// old Alert dialog became.
export type DockContext = {
  type: "decision" | "note";
  label: string;        // who it's from (display name)
  kind?: string;        // the recurring-stream label ("seat changes")
  fromAddr?: string;
  draftId?: string;     // decision: defer / dismiss target
  noteId?: string;      // note: settle target
  why?: string;         // note: why it surfaced
};

type Chip = { key: string; label: string; run: () => void };

// The orb never leaves on its own — only a tap or an explicit goodbye ends
// it. Quiet stretches just keep the ear open.

export function NanoOrb({
  apiUrl,
  auth,
  onNavigate,
  onRefreshInbox,
  onActed,
  openSignal,
  stageSignal,
  contextOpen,
}: {
  apiUrl: string;
  auth: Record<string, string>;
  onNavigate: (screen: string) => void;
  onRefreshInbox: () => void;
  onActed?: () => void;
  openSignal?: number;
  stageSignal?: number;
  contextOpen?: { seq: number; ctx: DockContext } | null;
}) {
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<OrbPhase>("idle");
  const [transcript, setTranscript] = useState("");
  const [say, setSay] = useState("");
  const [sayUrl, setSayUrl] = useState<string | null>(null);
  const [progress, setProgress] = useState(0); // interview progress, 0 hides the bar

  // Chat (type-to-ask) side of the dock, and the item it's about.
  const [dockMode, setDockMode] = useState<"voice" | "chat">("voice");
  const [ctx, setCtx] = useState<DockContext | null>(null);
  const [draft, setDraft] = useState("");
  const [chatWork, setChatWork] = useState<string | null>(null);
  const [chatReply, setChatReply] = useState<string | null>(null);
  const chatHist = useRef<ConverseTurn[]>([]);
  // The stage: full-screen conversation surface with live captions —
  // same session as the edge ball, bigger presence.
  const [stage, setStage] = useState(false);
  const [capWords, setCapWords] = useState<string[]>([]);
  const [capN, setCapN] = useState(0);
  const capTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const mode = useRef<OrbMode>("converse");
  const rtActive = useRef(false);
  const rtPoll = useRef<ReturnType<typeof setInterval> | null>(null);
  const rt = useConversation();
  const interviewSession = useRef<string | null>(null);
  const history = useRef<ConverseTurn[]>([]);
  const finalRef = useRef("");
  const emptyListens = useRef(0);
  const openRef = useRef(false);
  const speakTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reconnected = useRef(false);
  const startRealtimeRef = useRef<() => Promise<boolean>>(async () => false);
  const collapseRef = useRef<() => void>(() => {});

  const glide = useRef(new Animated.Value(0)).current;
  const breath = useRef(new Animated.Value(0)).current;

  const player = useAudioPlayer(sayUrl ? { uri: sayUrl, headers: auth } : null);
  useEffect(() => {
    if (sayUrl) {
      try {
        player.play();
      } catch {}
    }
  }, [sayUrl]);

  // Play through the speaker even when the ring/silent switch is on — without
  // this, Nano's captions animate but no sound comes out. (The #1 "it's not
  // speaking" cause on iOS.)
  useEffect(() => {
    setAudioModeAsync({ playsInSilentMode: true }).catch(() => {});
  }, []);

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
    if (rtPoll.current) clearInterval(rtPoll.current);
    if (rtActive.current) {
      rtActive.current = false;
      try {
        rt.endSession();
      } catch {}
    }
    try {
      ExpoSpeechRecognitionModule.stop();
    } catch {}
    Animated.timing(glide, {
      toValue: 0, duration: 380, easing: Easing.out(Easing.cubic), useNativeDriver: true,
    }).start(() => {
      setOpen(false);
      setPhase("idle");
      setTranscript("");
      setSay("");
      setSayUrl(null);
      setProgress(0);
      setCtx(null);
      setDraft("");
      setChatWork(null);
      setChatReply(null);
      setDockMode("voice");
      chatHist.current = [];
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
    if (!openRef.current || rtActive.current) return;
    const best = e.results?.[0]?.transcript ?? "";
    const text = (finalRef.current + " " + best).trim();
    setTranscript(text);
    if (e.isFinal) {
      finalRef.current = text;
      submit(text);
    }
  });
  useSpeechRecognitionEvent("end", () => {
    if (!openRef.current || rtActive.current || phase !== "listening") return;
    const heard = transcript.trim();
    if (heard && finalRef.current === "") {
      submit(heard);
    } else if (!heard) {
      listen(); // silence is fine — stay open, keep the ear on
    }
  });
  useSpeechRecognitionEvent("error", () => {
    if (!openRef.current || rtActive.current) return;
    if (phase === "listening") setTimeout(() => listen(), 600);
  });

  const startRealtime = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch(`${apiUrl}/v1/voice/realtime-token`, { headers: auth });
      if (!res.ok) return false;
      const { token } = await res.json();
      if (!token) return false;
      const userToken = (auth.Authorization ?? "").replace(/^Bearer\s+/, "");
      await rt.startSession({
        conversationToken: token,
        customLlmExtraBody: { user_token: userToken },
        onMessage: ({ message, source }: { message: string; source: string }) => {
          if (source === "user") setTranscript(message);
          else setSay(message);
        },
        onDisconnect: () => {
          if (!rtActive.current || !openRef.current) return;
          // Session died under us (quota, network). Try ONE silent
          // reconnect; if that fails, say so and hang up cleanly —
          // a zombie \"listening\" state is worse than an honest goodbye.
          rtActive.current = false;
          if (rtPoll.current) clearInterval(rtPoll.current);
          (async () => {
            if (!reconnected.current) {
              reconnected.current = true;
              if (await startRealtimeRef.current()) return;
            }
            setSay("The live line dropped. Tap the ball and we pick it right back up.");
            setTimeout(() => {
              if (openRef.current) collapseRef.current();
            }, 3500);
          })();
        },
        onError: () => {},
      } as never);
      rtActive.current = true;
      setPhase("listening");
      // Client-side effects (navigate, interview) queue on the server.
      rtPoll.current = setInterval(async () => {
        try {
          const r = await fetch(`${apiUrl}/v1/voice/pending-actions`, { headers: auth });
          const { actions } = await r.json();
          for (const a of actions ?? []) {
            if (a.type === "open_screen" && a.screen) onNavigate(a.screen);
            if (a.type === "refresh_inbox") onRefreshInbox();
            if (a.type === "start_interview") {
              rtActive.current = false;
              try {
                rt.endSession();
              } catch {}
              if (rtPoll.current) clearInterval(rtPoll.current);
              startInterviewInOrb();
            }
            onActed?.();
          }
        } catch {}
      }, 2500);
      return true;
    } catch {
      return false;
    }
  }, [apiUrl, auth, collapse, onActed, onNavigate, onRefreshInbox, rt, startInterviewInOrb]);

  const openOrb = useCallback(async () => {
    openRef.current = true;
    reconnected.current = false;
    emptyListens.current = 0;
    history.current = [];
    setDockMode("voice");
    setOpen(true);
    Animated.timing(glide, {
      toValue: 1, duration: 460, easing: Easing.out(Easing.back(1.4)), useNativeDriver: true,
    }).start();
    // Realtime first — live duplex talk. Fall back to the turn loop quietly.
    if (await startRealtime()) return;
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
  }, [apiUrl, auth, listen, speakThen, startRealtime]);

  // The app can summon Nano (e.g. "Set up with Nano" on the nutrition screen).
  useEffect(() => {
    if (openSignal && !openRef.current) openOrb();
  }, [openSignal]);

  useEffect(() => {
    if (!stageSignal) return;
    // Tapping the ball mid-session restarts it fresh — the honest cure for
    // \"it stopped hearing me\": tear everything down, come back listening.
    if (openRef.current) {
      collapseRef.current();
      setTimeout(() => openOrb(), 350);
    } else {
      openOrb();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stageSignal]);

  // There is no edge ball any more: the dock IS the conversation's presence.
  // It appears whenever the session is open and leaves when it ends — no
  // invisible listening, ever.
  useEffect(() => {
    setStage(open);
  }, [open]);

  // Captions: reveal Nano's words as it speaks; a fresh interruption wipes
  // the slate so the conversation restarts clean, like talking to a person.
  useEffect(() => {
    if (!stage || !say) return;
    const words = say.split(/\s+/).filter(Boolean);
    setCapWords(words);
    setCapN(0);
    if (capTimer.current) clearInterval(capTimer.current);
    capTimer.current = setInterval(() => {
      setCapN((n) => {
        if (n + 1 >= words.length) {
          if (capTimer.current) clearInterval(capTimer.current);
          return words.length;
        }
        return n + 1;
      });
    }, 130);
    return () => {
      if (capTimer.current) clearInterval(capTimer.current);
    };
  }, [say, stage]);

  useEffect(() => {
    if (!stage || !transcript) return;
    if (capTimer.current) clearInterval(capTimer.current);
    setCapWords([]);
    setCapN(0);
  }, [transcript, stage]);

  useEffect(() => {
    startRealtimeRef.current = startRealtime;
  }, [startRealtime]);
  useEffect(() => {
    collapseRef.current = collapse;
  }, [collapse]);

  // Closed: tap brings Nano over. Open: tap sends it back to the edge —
  // the only way it leaves besides an explicit goodbye.
  const onOrbTap = useCallback(() => {
    if (!openRef.current) openOrb();
    else collapse();
  }, [openOrb, collapse]);

  // ── Chat (type-to-ask) side ──────────────────────────────────────────────
  // Long-press on a card opens the dock in chat mode with that item as the
  // subject. No mic, no takeover — Nano comes to the thing you held.
  const openChat = useCallback((c: DockContext | null) => {
    if (rtActive.current) {
      rtActive.current = false;
      try { rt.endSession(); } catch {}
    }
    if (rtPoll.current) clearInterval(rtPoll.current);
    try { ExpoSpeechRecognitionModule.stop(); } catch {}
    openRef.current = false;
    chatHist.current = [];
    setCtx(c);
    setDockMode("chat");
    setDraft("");
    setChatWork(null);
    setChatReply(null);
    setSay("");
    setTranscript("");
    setCapWords([]);
    setPhase("idle");
    setOpen(true);
    Animated.timing(glide, {
      toValue: 1, duration: 460, easing: Easing.out(Easing.back(1.4)), useNativeDriver: true,
    }).start();
  }, [rt]);

  // Switch chat → voice: hand off to the live mic session, keeping the dock up.
  const goVoice = useCallback(() => {
    setDockMode("voice");
    setChatReply(null);
    setChatWork(null);
    openOrb();
  }, [openOrb]);

  // Switch voice → chat: drop the mic session, stay open as a text thread.
  const goChat = useCallback(() => {
    openRef.current = false;
    if (rtActive.current) {
      rtActive.current = false;
      try { rt.endSession(); } catch {}
    }
    if (rtPoll.current) clearInterval(rtPoll.current);
    try { ExpoSpeechRecognitionModule.stop(); } catch {}
    if (speakTimer.current) clearTimeout(speakTimer.current);
    setPhase("idle");
    setSay("");
    setSayUrl(null);
    setTranscript("");
    setCapWords([]);
    setDockMode("chat");
  }, [rt]);

  // A typed question — same brain as the orb, with the held item as context.
  const chatSend = useCallback(async (textArg?: string) => {
    const text = (typeof textArg === "string" ? textArg : draft).trim();
    if (!text) return;
    setDraft("");
    setChatReply(null);
    setChatWork(ctx ? "reading it across your inbox" : "reading your inbox");
    const prefix = ctx ? `About the ${ctx.type === "note" ? "note" : "email"} from ${ctx.label}: ` : "";
    chatHist.current = [...chatHist.current, { role: "user" as const, text: prefix + text }].slice(-16);
    try {
      const res = await fetch(`${apiUrl}/v1/voice/converse`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ messages: chatHist.current }),
      });
      const r = await res.json();
      const reply = r.say || "Say that once more?";
      chatHist.current = [...chatHist.current, { role: "nano" as const, text: reply }];
      setChatWork(null);
      setChatReply(reply);
      if (r.action === "open_screen" && r.screen) onNavigate(r.screen);
      if (r.action === "refresh_inbox") onRefreshInbox();
      if (r.acted) onActed?.();
    } catch {
      setChatWork(null);
      setChatReply("I couldn't reach the server just now — one more time?");
    }
  }, [apiUrl, auth, ctx, draft, onActed, onNavigate, onRefreshInbox]);

  // A contextual chip: do the real thing, then Nano confirms in its own words.
  const ctxAct = useCallback(
    async (confirm: string, req: { path: string; method?: string; body?: object } | null) => {
      setChatReply(null);
      setChatWork("applying it across your inbox");
      try {
        if (req) {
          await fetch(`${apiUrl}${req.path}`, {
            method: req.method ?? "POST",
            headers: { ...auth, "Content-Type": "application/json" },
            body: req.body ? JSON.stringify(req.body) : undefined,
          });
        }
      } catch { /* the confirm still shows; refresh reconciles */ }
      setChatWork(null);
      setChatReply(confirm);
      onActed?.();
    },
    [apiUrl, auth, onActed]
  );

  // The chips Nano offers for the held item — the old Alert options, now
  // spoken-to. Each does the real action and Nano confirms.
  const ctxChips = useCallback((): Chip[] => {
    if (!ctx) return [];
    const Kind = ctx.kind ? ctx.kind[0].toUpperCase() + ctx.kind.slice(1) : "This";
    if (ctx.type === "decision") {
      return [
        { key: "mute-sender", label: "Do not reply to this sender", run: () => ctxAct(
          `${ctx.label} comes straight to you from now on. I won't draft for them again.`,
          ctx.fromAddr ? { path: "/v1/inbox/mute", body: { sender: ctx.fromAddr } } : null) },
        ...(ctx.kind ? [{ key: "auto", label: "Auto-reply to these next time", run: () => ctxAct(
          "Done. I answer this kind myself from now on, signed as mine, and it lands under Worth knowing.",
          { path: "/v1/inbox/autoreply", body: { kind: ctx.kind } }) }] : []),
        ...(ctx.draftId ? [{ key: "hold", label: "Hold it until 6pm", run: () => ctxAct(
          "Held. I raise it once at 6pm and once tomorrow morning, then it's yours.",
          { path: `/v1/inbox/drafts/${ctx.draftId}/defer`,
            body: { tz_offset_minutes: new Date().getTimezoneOffset() } }) }] : []),
        ...(ctx.draftId ? [{ key: "never", label: "Never chase me about this", run: () => ctxAct(
          "No more reminders on this one. It stays in the Hub and I stay quiet.",
          { path: `/v1/inbox/drafts/${ctx.draftId}/dismiss` }) }] : []),
      ];
    }
    return [
      ...(ctx.kind ? [{ key: "mute-kind", label: "Do not show me these again", run: () => ctxAct(
        `Gone. ${Kind} won't reach you again.`,
        { path: "/v1/inbox/mute", body: { kind: ctx.kind } }) }] : []),
      { key: "mute-sender", label: "Never from this sender", run: () => ctxAct(
        `Nothing from ${ctx.label} will surface again. It still files, it just never speaks.`,
        ctx.fromAddr ? { path: "/v1/inbox/mute", body: { sender: ctx.fromAddr } } : null) },
      ...(ctx.kind ? [{ key: "auto", label: "Auto-reply to these next time", run: () => ctxAct(
        "Done. I answer this kind myself from now on and summarise it for you after.",
        { path: "/v1/inbox/autoreply", body: { kind: ctx.kind } }) }] : []),
      { key: "why", label: "Why did I see this?", run: () =>
        ctx.noteId ? void ctxAct(ctx.why || "Because it carried real information, but nothing you had to answer.", null)
                   : void chatSend("Why did I see this?") },
    ];
  }, [ctx, ctxAct, chatSend]);

  // Open in chat mode when a card is long-pressed (App relays the context).
  useEffect(() => {
    if (contextOpen && contextOpen.seq) openChat(contextOpen.ctx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextOpen?.seq]);

  useEffect(() => {
    if (!rtActive.current) return;
    setPhase(rt.isSpeaking ? "speaking" : "listening");
  }, [rt.isSpeaking]);

  const scale = breath.interpolate({ inputRange: [0, 1], outputRange: [1, phase === "listening" ? 1.16 : 1.06] });
  const orbTranslateX = glide.interpolate({ inputRange: [0, 1], outputRange: [0, -26] });
  const orbTranslateY = glide.interpolate({ inputRange: [0, 1], outputRange: [0, 34] });
  const orbScale = glide.interpolate({ inputRange: [0, 1], outputRange: [1, 1.55] });

  return (
    <>
    {stage ? (
      <View style={[o.vdock, { paddingBottom: 14 + Math.max(insets.bottom - 8, 0) }]}>
        <View style={o.vdockHead}>
          <View style={o.waveRow}>
            {[10, 16, 8, 14, 11, 17, 9].map((h, i) => (
              <Animated.View key={i} style={[o.waveBar, {
                height: dockMode === "chat" ? 4 : h,
                backgroundColor: dockMode === "chat" ? "rgba(255,255,255,0.2)" : "#C7B8FF",
                opacity: dockMode === "chat" ? 1 : breath.interpolate({
                  inputRange: [0, 1],
                  outputRange: i % 2 ? [0.35, 0.95] : [0.95, 0.35],
                }),
              }]} />
            ))}
          </View>
          <Text style={o.vdockLabel}>
            {dockMode === "chat"
              ? (chatWork ? "NANO  ·  THINKING" : "NANO  ·  TYPE TO ASK")
              : `NANO  ·  ${phase === "speaking" ? "SPEAKING" : phase === "thinking" ? "THINKING" : "LISTENING"}`}
          </Text>
          <Pressable
            style={o.modePill}
            onPress={() => (dockMode === "chat" ? goVoice() : goChat())}
            hitSlop={8}
          >
            <Text style={o.modePillText}>{dockMode === "chat" ? "SPEAK" : "TYPE"}</Text>
          </Pressable>
          <Pressable onPress={collapse} hitSlop={12} style={o.closeDot}>
            <Text style={{ color: "rgba(244,242,250,0.6)", fontSize: 13 }}>✕</Text>
          </Pressable>
        </View>

        {progress > 0 ? (
          <View style={o.vdockProgress}>
            <View style={[o.vdockProgressFill, { width: `${Math.round(progress * 100)}%` }]} />
          </View>
        ) : null}

        {dockMode === "voice" ? (
          <>
            {transcript ? (
              <Text style={o.vdockHeard} numberOfLines={1}>“{transcript}”</Text>
            ) : null}
            {capWords.length ? (
              <Text style={o.vdockCaption}>
                <Text style={o.capSaid}>{capWords.slice(0, capN + 1).join(" ")}</Text>
                {capN + 1 < capWords.length ? (
                  <Text style={o.capRest}> {capWords.slice(capN + 1).join(" ")}</Text>
                ) : null}
              </Text>
            ) : !transcript ? (
              <Text style={o.vdockHint}>Say it — I'm listening.</Text>
            ) : null}
          </>
        ) : (
          <View>
            {ctx ? (
              <View style={o.ctxChip}>
                <View style={o.ctxDot} />
                <Text style={o.ctxChipText} numberOfLines={1}>About {ctx.label}</Text>
                <Pressable onPress={() => { setCtx(null); setChatReply(null); setChatWork(null); }} hitSlop={8}>
                  <Text style={o.ctxClear}>Clear</Text>
                </Pressable>
              </View>
            ) : null}

            {chatWork ? (
              <Text style={o.chatWork}>{chatWork}…</Text>
            ) : chatReply ? (
              <Text style={o.chatReply}>{chatReply}</Text>
            ) : null}

            <ScrollView horizontal showsHorizontalScrollIndicator={false}
                        contentContainerStyle={o.chipRow} keyboardShouldPersistTaps="handled">
              {(ctx ? ctxChips()
                    : ["What needs me?", "Summarise my inbox", "What did you send?"].map((q) => (
                        { key: q, label: q, run: () => chatSend(q) } as Chip
                      ))
              ).map((c) => (
                <Pressable key={c.key} style={o.promptChip} onPress={c.run}>
                  <Text style={o.promptChipText}>{c.label}</Text>
                </Pressable>
              ))}
            </ScrollView>

            <View style={o.inputRow}>
              <TextInput
                style={o.input}
                value={draft}
                onChangeText={setDraft}
                onSubmitEditing={() => chatSend()}
                placeholder="Ask about your inbox"
                placeholderTextColor="rgba(244,242,250,0.4)"
                returnKeyType="send"
              />
              <Pressable
                onPress={() => chatSend()}
                style={[o.sendDot, { backgroundColor: draft.trim() ? "#C7B8FF" : "rgba(255,255,255,0.1)" }]}
              >
                <Text style={{ color: draft.trim() ? "#14101F" : "rgba(244,242,250,0.5)", fontSize: 16, marginTop: -1 }}>↑</Text>
              </Pressable>
            </View>
          </View>
        )}
      </View>
    ) : null}
    </>
  );
}

const o = StyleSheet.create({
  vdock: {
    position: "absolute",
    left: 12, right: 12, bottom: 24,
    backgroundColor: "rgba(14,12,24,0.98)",
    borderWidth: 1, borderColor: "rgba(199,184,255,0.16)",
    borderRadius: 22,
    paddingHorizontal: 18, paddingVertical: 14,
    zIndex: 60,
    shadowColor: "#000", shadowOpacity: 0.5, shadowRadius: 24,
    shadowOffset: { width: 0, height: 8 },
  },
  vdockHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  waveRow: { flexDirection: "row", alignItems: "center", gap: 2.5, height: 18 },
  waveBar: { width: 2.5, borderRadius: 2, backgroundColor: "#C7B8FF" },
  vdockLabel: {
    flex: 1,
    fontFamily: "JetBrainsMono_400Regular", fontSize: 10, letterSpacing: 3,
    color: "#8A87A3", marginLeft: 4,
  },
  modePill: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.14)",
  },
  modePillText: {
    fontFamily: "JetBrainsMono_400Regular", fontSize: 9.5, letterSpacing: 1.5,
    color: "rgba(199,184,255,0.9)",
  },
  closeDot: {
    width: 22, height: 22, borderRadius: 11,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.07)",
  },
  ctxChip: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 100,
    backgroundColor: "rgba(199,184,255,0.1)",
    borderWidth: 1, borderColor: "rgba(199,184,255,0.22)",
    alignSelf: "flex-start", maxWidth: "100%", marginTop: 13,
  },
  ctxDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: "#C7B8FF" },
  ctxChipText: {
    flexShrink: 1, fontFamily: "InstrumentSans_400Regular", fontSize: 11.5,
    color: "rgba(244,242,250,0.8)",
  },
  ctxClear: {
    fontFamily: "InstrumentSans_600SemiBold", fontSize: 11.5,
    color: "rgba(199,184,255,0.85)",
  },
  chatWork: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 13,
    color: "rgba(199,184,255,0.7)", marginTop: 12, fontStyle: "italic",
  },
  chatReply: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 15.5, lineHeight: 22,
    color: "#F4F2FA", marginTop: 12,
  },
  chipRow: { gap: 7, paddingTop: 14, paddingBottom: 2, paddingRight: 4 },
  promptChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.05)",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
  },
  promptChipText: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 11.5,
    color: "rgba(244,242,250,0.8)",
  },
  inputRow: {
    flexDirection: "row", alignItems: "center", gap: 9,
    marginTop: 10, paddingLeft: 15, paddingRight: 7, paddingVertical: 7,
    borderRadius: 100,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
  },
  input: {
    flex: 1, minWidth: 0, padding: 0,
    fontFamily: "InstrumentSans_400Regular", fontSize: 13.5, color: "#F4F2FA",
  },
  sendDot: {
    width: 31, height: 31, borderRadius: 16,
    alignItems: "center", justifyContent: "center",
  },
  vdockProgress: {
    height: 2, borderRadius: 1, marginTop: 12,
    backgroundColor: "rgba(199,184,255,0.18)", overflow: "hidden",
  },
  vdockProgressFill: { height: 2, backgroundColor: "#C7B8FF" },
  vdockHeard: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 13,
    color: "#8A87A3", marginTop: 10,
  },
  vdockCaption: { marginTop: 8, fontSize: 19, lineHeight: 27 },
  capSaid: { fontFamily: "InstrumentSans_600SemiBold", color: "#F4F2FA" },
  capRest: { fontFamily: "InstrumentSans_400Regular", color: "rgba(244,242,250,0.32)" },
  vdockHint: {
    fontFamily: "InstrumentSans_400Regular", fontSize: 14,
    color: "#8A87A3", marginTop: 10,
  },
  dock: {
    position: "absolute",
    right: -14, // half-tucked into the edge when idle
    alignItems: "flex-end",
    zIndex: 50,
  },
  orb: {
    width: 34,
    height: 34,
    borderRadius: 17,
    shadowColor: "#C7B8FF",
    shadowOpacity: 0.55,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 0 },
  },
  bubble: {
    position: "absolute",
    top: 96,
    right: 34,
    maxWidth: 290,
    backgroundColor: "rgba(20,16,31,0.96)",
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.16)",
    borderRadius: 18,
    borderTopRightRadius: 4,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  progressTrack: {
    alignSelf: "stretch",
    height: 2,
    borderRadius: 1,
    backgroundColor: "rgba(199,184,255,0.18)",
    marginBottom: 10,
    overflow: "hidden",
  },
  progressFill: { height: 2, backgroundColor: "#C7B8FF" },
  status: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 12,
    letterSpacing: 4,
    color: "#8A87A3",
  },
  say: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 17,
    lineHeight: 23,
    color: "#F4F2FA",
  },
  transcript: {
    fontFamily: "InstrumentSerif_400Regular",
    fontSize: 16,
    lineHeight: 22,
    color: "#C7B8FF",
  },
});
