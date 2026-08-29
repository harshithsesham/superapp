// Cal — the nutrition vertical, rebuilt native from the Cal Neo design.
// One component owns the whole vertical: 4-step onboarding, the 3-panel home,
// and settings. The server stays the brain (/v1/nutrition/state|onboard|
// suggest|water|log|photo|meals/:id/fix|reset); this file is the exact skin.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Dimensions,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import * as ImagePicker from "expo-image-picker";
import Svg, { Circle } from "react-native-svg";

type Auth = Record<string, string>;

type CalState = {
  onboarded: boolean;
  plan: null | {
    kcal: number;
    protein_g: number;
    carbs_g: number;
    fat_g: number;
    water_ml: number;
    fiber_g?: number;
    sugar_g_max?: number;
    sodium_mg_max?: number;
    steps_target?: number;
    started?: string;
  };
  profile: null | { born_year?: number; height_cm?: number; weight_kg?: number; steps_target?: number };
  today: {
    date: string;
    kcal: number;
    protein_g: number;
    carbs_g: number;
    fat_g: number;
    fiber_g?: number;
    sugar_g?: number;
    sodium_mg?: number;
    water_ml: number;
    meals: {
      id: string;
      description: string;
      kcal: number | null;
      protein_g: number | null;
      carbs_g: number | null;
      fat_g: number | null;
      logged_at: string;
    }[];
  };
  week: { date: string; day: string; kcal: number; meals: number }[];
  activity: null | { steps: number; active_kcal: number };
  day_n: number;
  health: { score: number; note: string };
  summary: string;
};

const C = {
  ground: "#08070E",
  card: "#14101F",
  card2: "#1B1530",
  line: "rgba(199,184,255,0.14)",
  ink: "#F4F2FA",
  body: "#B9B4CC",
  dim: "#8A87A3",
  lav: "#C7B8FF",
  lavDeep: "#8B7CF6",
  mint: "#7CF7C4",
  rose: "#FF9DA8",
  amber: "#F5C97B",
};

const STARS = [
  [24, 90], [310, 60], [120, 180], [355, 260], [60, 330], [250, 300],
  [180, 120], [30, 480], [330, 470], [90, 560], [280, 600], [160, 660],
];

function Starfield() {
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      {STARS.map(([x, y], i) => (
        <View
          key={i}
          style={{
            position: "absolute",
            left: x,
            top: y,
            width: i % 3 === 0 ? 2.5 : 1.5,
            height: i % 3 === 0 ? 2.5 : 1.5,
            borderRadius: 2,
            backgroundColor: "#CFC6EF",
            opacity: i % 2 === 0 ? 0.5 : 0.25,
          }}
        />
      ))}
    </View>
  );
}

function Planet({ size }: { size: number }) {
  return (
    <LinearGradient
      colors={["#E8E2FF", "#8B7CF6", "#2A2050"]}
      start={{ x: 0.25, y: 0.15 }}
      end={{ x: 0.8, y: 0.95 }}
      style={{ width: size, height: size, borderRadius: size / 2 }}
    />
  );
}

function GradientButton({ label, onPress, icon }: { label: string; onPress: () => void; icon?: string }) {
  return (
    <Pressable onPress={onPress} style={{ flex: 1 }}>
      <LinearGradient
        colors={["#EFEAFF", "#C3B4FF"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={s.gbtn}
      >
        <Text style={s.gbtnText}>
          {icon ? `${icon}  ` : ""}
          {label}
        </Text>
      </LinearGradient>
    </Pressable>
  );
}

function Ring({ size, stroke, pct, color, track }: { size: number; stroke: number; pct: number; color: string; track?: string }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, pct));
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <Circle cx={size / 2} cy={size / 2} r={r} stroke={track ?? "rgba(199,184,255,0.14)"} strokeWidth={stroke} fill="none" />
      <Circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        stroke={color}
        strokeWidth={stroke}
        fill="none"
        strokeLinecap="round"
        strokeDasharray={`${circ * p} ${circ}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </Svg>
  );
}

function Stepper({
  label,
  value,
  onDelta,
}: {
  label: string;
  value: string;
  onDelta: (d: 1 | -1) => void;
}) {
  return (
    <View style={s.stepRow}>
      <Text style={s.stepLabel}>{label}</Text>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
        <Pressable onPress={() => onDelta(-1)} style={s.stepBtn} hitSlop={8}>
          <Text style={s.stepBtnText}>−</Text>
        </Pressable>
        <Text style={s.stepValue}>{value}</Text>
        <Pressable onPress={() => onDelta(1)} style={[s.stepBtn, s.stepBtnPlus]} hitSlop={8}>
          <Text style={s.stepBtnText}>+</Text>
        </Pressable>
      </View>
    </View>
  );
}

export function CalScreen({
  apiUrl,
  auth,
  onOpenOrb,
  onConnectHealth,
}: {
  apiUrl: string;
  auth: Auth;
  onOpenOrb: () => void;
  onConnectHealth: () => void;
}) {
  const [state, setState] = useState<CalState | null>(null);
  const [view, setView] = useState<"home" | "settings">("home");
  const [panel, setPanel] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [openMeal, setOpenMeal] = useState<string | null>(null);
  const pagerRef = useRef<ScrollView>(null);
  const stripRef = useRef<ScrollView>(null);

  const say = useCallback((t: string) => {
    setToast(t);
    setTimeout(() => setToast(null), 3500);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/v1/nutrition/state`, { headers: auth });
      if (res.ok) setState(await res.json());
    } catch {}
    setRefreshing(false);
  }, [apiUrl, auth]);

  useEffect(() => {
    load();
  }, [load]);

  const post = useCallback(
    async (path: string, body?: object) => {
      setBusy(true);
      try {
        await fetch(`${apiUrl}${path}`, {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        await load();
      } catch {}
      setBusy(false);
    },
    [apiUrl, auth, load]
  );

  const snapPlate = useCallback(async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    const pick = perm.granted
      ? await ImagePicker.launchCameraAsync({ quality: 0.7 })
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.7 });
    if (pick.canceled || !pick.assets?.[0]) return;
    setBusy(true);
    say("Reading the plate…");
    try {
      const asset = pick.assets[0];
      const form = new FormData();
      form.append("photo", {
        uri: asset.uri,
        name: "meal.jpg",
        type: "image/jpeg",
      } as unknown as Blob);
      await fetch(`${apiUrl}/v1/nutrition/photo`, { method: "POST", headers: auth, body: form });
      await load();
      say("Logged. The count is honest.");
    } catch {
      say("Couldn't read that one — try again?");
    }
    setBusy(false);
  }, [apiUrl, auth, load, say]);

  const logText = useCallback(() => {
    Alert.prompt("Describe the meal", "e.g. 2 eggs and toast", async (text) => {
      if (!text?.trim()) return;
      setBusy(true);
      try {
        await fetch(`${apiUrl}/v1/nutrition/log`, {
          method: "POST",
          headers: { ...auth, "Content-Type": "application/json" },
          body: JSON.stringify({ description: text.trim() }),
        });
        await load();
      } catch {}
      setBusy(false);
    });
  }, [apiUrl, auth, load]);

  const fixMeal = useCallback(
    (id: string) => {
      Alert.prompt("Fix this estimate", "What's wrong? e.g. 'it was mutton, not veg'", async (note) => {
        if (!note?.trim()) return;
        await post(`/v1/nutrition/meals/${id}/fix`, { note: note.trim() });
      });
    },
    [post]
  );

  if (!state) {
    return (
      <View style={[s.fill, { alignItems: "center", justifyContent: "center" }]}>
        <ActivityIndicator color={C.lav} />
      </View>
    );
  }

  if (!state.onboarded) {
    return <CalOnboarding apiUrl={apiUrl} auth={auth} onDone={(kcal) => {
      load();
      say(`Welcome. ${kcal.toLocaleString()} kcal a day — I'll keep the count.`);
    }} />;
  }

  const plan = state.plan!;
  const t = state.today;
  const left = Math.max(plan.kcal - t.kcal, 0);
  const eatenPct = plan.kcal ? Math.min(t.kcal / plan.kcal, 1) : 0;

  if (view === "settings") {
    return (
      <CalSettings
        state={state}
        onBack={() => {
          setView("home");
          load();
        }}
        onPatch={(body) => post("/v1/nutrition/onboard", body)}
        onConnectHealth={onConnectHealth}
        onRestart={async () => {
          await post("/v1/nutrition/reset");
          setView("home");
        }}
      />
    );
  }

  return (
    <View style={s.fill}>
      <Starfield />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 130 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={C.lav} onRefresh={() => {
            setRefreshing(true);
            load();
          }} />
        }
      >
        <View style={s.header}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <Pressable onPress={onOpenOrb}>
              <Planet size={26} />
            </Pressable>
            <Text style={s.brand}>Cal</Text>
          </View>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            {state.day_n > 0 ? <Text style={s.dayChip}>● DAY {state.day_n}</Text> : null}
            <Pressable onPress={() => setView("settings")} style={s.personBtn} hitSlop={8}>
              <Text style={{ color: C.body, fontSize: 15 }}>⍜</Text>
            </Pressable>
          </View>
        </View>

        <ScrollView
          ref={stripRef}
          horizontal
          showsHorizontalScrollIndicator={false}
          onContentSizeChange={() => stripRef.current?.scrollToEnd({ animated: false })}
          contentContainerStyle={{ gap: 6, paddingHorizontal: 16 }}
          style={{ marginTop: 6 }}
        >
          {state.week.map((d, i) => {
            const isToday = d.date === t.date;
            return (
              <View key={i} style={[s.dayCell, isToday && s.dayCellToday]}>
                <Text style={[s.dayLetter, isToday && { color: "#E9E4FF" }]}>{d.day[0]}</Text>
                <Text style={[s.dayNum, isToday && { color: "#E9E4FF" }]}>{String(+d.date.slice(-2))}</Text>
                <View style={[s.dayDot, { opacity: d.meals > 0 ? 1 : 0 }]} />
              </View>
            );
          })}
        </ScrollView>

        <ScrollView
          ref={pagerRef}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          onMomentumScrollEnd={(e) =>
            setPanel(Math.round(e.nativeEvent.contentOffset.x / e.nativeEvent.layoutMeasurement.width))
          }
          style={{ marginTop: 12 }}
        >
          {/* Panel 1 — the budget */}
          <View style={s.panel}>
            <LinearGradient
              colors={["#2A2050", "#151024"]}
              start={{ x: 0.15, y: 0 }}
              end={{ x: 0.7, y: 1 }}
              style={s.heroCard}
            >
              <View style={{ flex: 1 }}>
                <Text style={s.heroBig}>{left.toLocaleString()}</Text>
                <Text style={s.monoLabel}>KCAL LEFT OF {plan.kcal.toLocaleString()}</Text>
              </View>
              <View style={{ width: 76, height: 76, alignItems: "center", justifyContent: "center" }}>
                <Ring size={76} stroke={7} pct={eatenPct} color={C.lavDeep} />
                <Text style={[s.ringPct, { position: "absolute" }]}>{Math.round(eatenPct * 100)}%</Text>
              </View>
            </LinearGradient>
            <View style={s.trioRow}>
              {[
                { v: Math.max(plan.protein_g - (t.protein_g || 0), 0), l: "PROTEIN LEFT", c: C.lavDeep, pct: (t.protein_g || 0) / plan.protein_g, u: "g" },
                { v: Math.max(plan.carbs_g - (t.carbs_g || 0), 0), l: "CARBS LEFT", c: C.amber, pct: (t.carbs_g || 0) / plan.carbs_g, u: "g" },
                { v: Math.max(plan.fat_g - (t.fat_g || 0), 0), l: "FAT LEFT", c: C.mint, pct: (t.fat_g || 0) / plan.fat_g, u: "g" },
              ].map((m, i) => (
                <View key={i} style={s.trioCard}>
                  <Text style={s.trioValue}>{Math.round(m.v)}{m.u}</Text>
                  <Text style={s.trioLabel}>{m.l}</Text>
                  <View style={{ marginTop: 10 }}>
                    <Ring size={30} stroke={4} pct={m.pct} color={m.c} />
                  </View>
                </View>
              ))}
            </View>
          </View>

          {/* Panel 2 — the quiet targets + health score */}
          <View style={s.panel}>
            <View style={s.trioRow}>
              {[
                { v: Math.max((plan.fiber_g ?? 30) - (t.fiber_g || 0), 0), l: "FIBER LEFT", c: C.lavDeep, pct: (t.fiber_g || 0) / (plan.fiber_g ?? 30), u: "g" },
                { v: Math.max((plan.sugar_g_max ?? 55) - (t.sugar_g || 0), 0), l: "SUGAR LEFT", c: C.rose, pct: (t.sugar_g || 0) / (plan.sugar_g_max ?? 55), u: "g" },
                { v: Math.max((plan.sodium_mg_max ?? 2300) - (t.sodium_mg || 0), 0), l: "SODIUM LEFT", c: C.amber, pct: (t.sodium_mg || 0) / (plan.sodium_mg_max ?? 2300), u: "mg" },
              ].map((m, i) => (
                <View key={i} style={s.trioCard}>
                  <Text style={s.trioValue}>{Math.round(m.v)}{m.u}</Text>
                  <Text style={s.trioLabel}>{m.l}</Text>
                  <View style={{ marginTop: 10 }}>
                    <Ring size={30} stroke={4} pct={m.pct} color={m.c} />
                  </View>
                </View>
              ))}
            </View>
            <LinearGradient
              colors={["#221B3A", "#141021"]}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={s.healthCard}
            >
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" }}>
                <Text style={s.healthTitle}>Health score</Text>
                <Text style={s.healthValue}>{state.health.score < 0 ? "—" : `${state.health.score} / 100`}</Text>
              </View>
              <View style={s.healthTrack}>
                <LinearGradient
                  colors={[C.lavDeep, C.mint]}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 0 }}
                  style={{ height: 4, borderRadius: 2, width: `${Math.max(state.health.score, 0)}%` }}
                />
              </View>
              <Text style={s.healthNote}>{state.health.note}</Text>
            </LinearGradient>
          </View>

          {/* Panel 3 — health connect + water */}
          <View style={[s.panel, { flexDirection: "row", gap: 10 }]}>
            <View style={[s.trioCard, { flex: 1, alignItems: "center", paddingVertical: 22 }]}>
              <Text style={{ fontSize: 20 }}>{state.activity ? "🫀" : "♡"}</Text>
              {state.activity ? (
                <>
                  <Text style={[s.trioValue, { marginTop: 8 }]}>{state.activity.steps.toLocaleString()}</Text>
                  <Text style={s.trioLabel}>STEPS · {state.activity.active_kcal} KCAL BURN</Text>
                </>
              ) : (
                <>
                  <Text style={[s.healthTitle, { marginTop: 8 }]}>Connect Health</Text>
                  <Text style={s.trioLabel}>STEPS &amp; BURN</Text>
                  <Pressable onPress={onConnectHealth} style={s.smallBtnLav}>
                    <Text style={s.smallBtnLavText}>Connect</Text>
                  </Pressable>
                </>
              )}
            </View>
            <View style={[s.trioCard, { flex: 1.2, paddingVertical: 18 }]}>
              <Text style={s.trioLabel}>WATER</Text>
              <Text style={[s.trioValue, { marginTop: 6 }]}>
                {t.water_ml}
                <Text style={{ color: C.dim, fontSize: 13 }}> / {plan.water_ml} ml</Text>
              </Text>
              <View style={[s.healthTrack, { marginTop: 12 }]}>
                <View style={{ height: 4, borderRadius: 2, backgroundColor: C.mint, width: `${Math.min((t.water_ml / plan.water_ml) * 100, 100)}%` }} />
              </View>
              <Pressable onPress={() => post("/v1/nutrition/water", { ml: 250 })} style={s.smallBtn}>
                <Text style={s.smallBtnText}>Log water</Text>
              </Pressable>
            </View>
          </View>
        </ScrollView>

        <View style={s.dots}>
          {[0, 1, 2].map((i) => (
            <View key={i} style={[s.dot, panel === i && s.dotActive]} />
          ))}
        </View>

        <Text style={[s.monoLabel, { marginHorizontal: 20, marginTop: 18 }]}>
          TODAY · {t.meals.length} LOGGED
        </Text>
        <View style={{ paddingHorizontal: 16, marginTop: 8, gap: 8 }}>
          {t.meals.length === 0 ? (
            <Text style={{ color: C.dim, paddingHorizontal: 6 }}>Nothing yet. Snap your first plate.</Text>
          ) : (
            t.meals.map((m) => (
              <Pressable key={m.id} onPress={() => setOpenMeal(openMeal === m.id ? null : m.id)} style={s.mealRow}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <View style={s.mealTile}>
                    <Text style={s.mealTileText}>{(m.description || "M")[0].toUpperCase()}</Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={s.mealName} numberOfLines={1}>{m.description || "Meal"}</Text>
                    <Text style={s.mealMacros}>
                      {m.kcal ?? "…"} kcal  P {Math.round(m.protein_g || 0)}g  C {Math.round(m.carbs_g || 0)}g  F {Math.round(m.fat_g || 0)}g
                    </Text>
                  </View>
                  <Text style={s.mealTime}>{m.logged_at.slice(11, 16)}</Text>
                  <Text style={{ color: C.dim, marginLeft: 8 }}>{openMeal === m.id ? "▾" : "›"}</Text>
                </View>
                {openMeal === m.id ? (
                  <Pressable onPress={() => fixMeal(m.id)} hitSlop={8}>
                    <Text style={s.fixLink}>✦ FIX THIS ESTIMATE</Text>
                  </Pressable>
                ) : null}
              </Pressable>
            ))
          )}
        </View>
      </ScrollView>

      <View style={s.ctaBar}>
        <GradientButton icon="📷" label={busy ? "Working…" : "Snap the plate"} onPress={snapPlate} />
        <Pressable onPress={logText} style={s.scanBtn} hitSlop={8}>
          <Text style={{ color: C.body, fontSize: 15 }}>✎</Text>
        </Pressable>
      </View>

      {toast ? (
        <View style={s.toast}>
          <Text style={{ color: C.mint, marginRight: 8 }}>✓</Text>
          <Text style={{ color: C.body, fontSize: 13, flex: 1 }}>{toast}</Text>
        </View>
      ) : null}
    </View>
  );
}

function CalOnboarding({ apiUrl, auth, onDone }: { apiUrl: string; auth: Auth; onDone: (kcal: number) => void }) {
  const [step, setStep] = useState(0);
  const [born, setBorn] = useState(2000);
  const [height, setHeight] = useState(172);
  const [weight, setWeight] = useState(70);
  const [steps, setSteps] = useState(10000);
  const [kcal, setKcal] = useState<number | null>(null);
  const [water, setWater] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const age = new Date().getFullYear() - born;
  const bmi = useMemo(() => weight / Math.pow(height / 100, 2), [weight, height]);
  const bmiBand = bmi < 18.5 ? "LOW" : bmi < 25 ? "HEALTHY" : bmi < 30 ? "HIGH" : "VERY HIGH";

  const suggest = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/v1/nutrition/suggest`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({ born_year: born, height_cm: height, weight_kg: weight, steps_target: steps }),
      });
      if (res.ok) {
        const r = await res.json();
        setKcal((k) => k ?? r.kcal);
        setWater((w) => w ?? r.water_ml);
      }
    } catch {}
  }, [apiUrl, auth, born, height, weight, steps]);

  const finish = useCallback(async () => {
    setBusy(true);
    try {
      await fetch(`${apiUrl}/v1/nutrition/onboard`, {
        method: "POST",
        headers: { ...auth, "Content-Type": "application/json" },
        body: JSON.stringify({
          born_year: born,
          height_cm: height,
          weight_kg: weight,
          steps_target: steps,
          kcal_override: kcal ?? undefined,
          water_override: water ?? undefined,
        }),
      });
      onDone(kcal ?? 0);
    } catch {}
    setBusy(false);
  }, [apiUrl, auth, born, height, weight, steps, kcal, water, onDone]);

  const next = () => {
    if (step === 1) suggest();
    setStep(step + 1);
  };

  return (
    <View style={s.fill}>
      <Starfield />
      <View style={s.obProgress}>
        {[0, 1, 2, 3].map((i) => (
          <View key={i} style={[s.obSeg, i <= step && s.obSegOn]} />
        ))}
      </View>

      {step === 0 && (
        <View style={[s.fill, { alignItems: "center", justifyContent: "center", padding: 32 }]}>
          <Planet size={88} />
          <Text style={s.obBrand}>Cal</Text>
          <Text style={s.obTagline}>
            Point it at a plate. It reads the food, keeps the count, and stays out of your way.
          </Text>
        </View>
      )}

      {step === 1 && (
        <View style={s.obBody}>
          <Text style={s.obTitle}>About you</Text>
          <Text style={s.obSub}>Sets your calorie budget and BMI. Editable any time in settings.</Text>
          <View style={{ gap: 10, marginTop: 18 }}>
            <Stepper label={`Birth year (age ${age})`} value={String(born)} onDelta={(d) => setBorn(Math.min(Math.max(born + d, 1930), 2015))} />
            <Stepper label="Height" value={`${height} cm`} onDelta={(d) => setHeight(Math.min(Math.max(height + d, 120), 220))} />
            <Stepper label="Weight" value={`${weight} kg`} onDelta={(d) => setWeight(Math.min(Math.max(weight + d, 30), 300))} />
            <View style={[s.stepRow, { borderColor: "rgba(124,247,196,0.25)" }]}>
              <Text style={s.stepLabel}>Your BMI</Text>
              <Text style={s.stepValue}>
                {bmi.toFixed(1)}  <Text style={{ color: C.mint, fontSize: 10, letterSpacing: 2 }}>{bmiBand}</Text>
              </Text>
            </View>
          </View>
        </View>
      )}

      {step === 2 && (
        <View style={s.obBody}>
          <Text style={s.obTitle}>Your targets</Text>
          <Text style={s.obSub}>Suggested from your body numbers — nudge them if you like.</Text>
          <Text style={[s.monoLabel, { marginTop: 18 }]}>DAILY STEPS</Text>
          <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
            {[6000, 8000, 10000, 12000].map((v) => (
              <Pressable
                key={v}
                onPress={() => {
                  setSteps(v);
                  setKcal(null);
                  setWater(null);
                  setTimeout(suggest, 50);
                }}
                style={[s.chip, steps === v && s.chipOn]}
              >
                <Text style={[s.chipText, steps === v && { color: "#E9E4FF" }]}>{v / 1000}k</Text>
              </Pressable>
            ))}
          </View>
          <View style={{ gap: 10, marginTop: 14 }}>
            <Stepper label="Calorie budget" value={String(kcal ?? "…")} onDelta={(d) => setKcal(Math.min(Math.max((kcal ?? 2200) + d * 50, 1200), 4000))} />
            <Stepper label="Water target" value={`${water ?? "…"} mL`} onDelta={(d) => setWater(Math.min(Math.max((water ?? 2500) + d * 100, 1500), 5000))} />
          </View>
        </View>
      )}

      {step === 3 && (
        <View style={s.obBody}>
          <Text style={s.obTitle}>All set</Text>
          <Text style={s.obSub}>Here's the day Cal will keep for you.</Text>
          <LinearGradient colors={["#221B3A", "#141021"]} style={[s.healthCard, { marginTop: 18 }]}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline" }}>
              <Text style={s.summaryKcal}>{(kcal ?? 0).toLocaleString()}</Text>
              <Text style={s.monoLabel}>KCAL / DAY</Text>
            </View>
            <View style={{ marginTop: 12, gap: 7 }}>
              {[
                ["Steps target", steps.toLocaleString()],
                ["Water target", `${water ?? "—"} mL`],
                ["BMI", `${bmi.toFixed(1)} · ${bmiBand.charAt(0) + bmiBand.slice(1).toLowerCase()} range`],
              ].map(([k, v], i) => (
                <View key={i} style={{ flexDirection: "row", justifyContent: "space-between" }}>
                  <Text style={{ color: C.dim, fontSize: 13.5 }}>{k}</Text>
                  <Text style={{ color: C.ink, fontSize: 13.5, fontWeight: "600" }}>{v}</Text>
                </View>
              ))}
            </View>
          </LinearGradient>
          <Text style={s.obEpigraph}>No streaks. No guilt. Just an honest count.</Text>
        </View>
      )}

      <View style={s.obFooter}>
        {step > 0 ? (
          <Pressable onPress={() => setStep(step - 1)} style={s.backBtn} hitSlop={8}>
            <Text style={{ color: C.body, fontSize: 16 }}>‹</Text>
          </Pressable>
        ) : null}
        <GradientButton
          label={busy ? "…" : step === 0 ? "Get started" : step === 3 ? "Enter Cal" : "Continue"}
          onPress={step === 3 ? finish : next}
        />
      </View>
    </View>
  );
}

function CalSettings({
  state,
  onBack,
  onPatch,
  onConnectHealth,
  onRestart,
}: {
  state: CalState;
  onBack: () => void;
  onPatch: (body: object) => void;
  onConnectHealth: () => void;
  onRestart: () => void;
}) {
  const p = state.profile ?? {};
  const plan = state.plan!;
  const bmi = p.weight_kg && p.height_cm ? p.weight_kg / Math.pow(p.height_cm / 100, 2) : null;
  return (
    <View style={s.fill}>
      <Starfield />
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 60 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12, marginTop: 6 }}>
          <Pressable onPress={onBack} style={s.backBtn} hitSlop={8}>
            <Text style={{ color: C.body, fontSize: 16 }}>‹</Text>
          </Pressable>
          <Text style={s.obTitle}>Settings</Text>
        </View>

        <LinearGradient colors={["#2A2050", "#151024"]} style={[s.heroCard, { marginTop: 16 }]}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12, flex: 1 }}>
            <Planet size={40} />
            <View>
              <Text style={s.healthTitle}>Your body</Text>
              <Text style={s.trioLabel}>
                {bmi ? `BMI ${bmi.toFixed(1)} · ${bmi < 25 ? "HEALTHY" : "WATCH"}` : "SET YOUR NUMBERS"}
              </Text>
            </View>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <Text style={s.summaryKcal}>{plan.kcal.toLocaleString()}</Text>
            <Text style={s.trioLabel}>KCAL / DAY</Text>
          </View>
        </LinearGradient>

        <Text style={[s.monoLabel, { marginTop: 20 }]}>PERSONAL DETAILS</Text>
        <View style={{ gap: 10, marginTop: 8 }}>
          <Stepper label={`Birth year (age ${p.born_year ? new Date().getFullYear() - p.born_year : "—"})`} value={String(p.born_year ?? "—")} onDelta={(d) => p.born_year && onPatch({ born_year: p.born_year + d })} />
          <Stepper label="Height" value={`${p.height_cm ?? "—"} cm`} onDelta={(d) => p.height_cm && onPatch({ height_cm: p.height_cm + d })} />
          <Stepper label="Weight" value={`${p.weight_kg ?? "—"} kg`} onDelta={(d) => p.weight_kg && onPatch({ weight_kg: p.weight_kg + d })} />
          <Stepper label="Target steps" value={`${((plan.steps_target ?? 10000) / 1000)}k`} onDelta={(d) => onPatch({ steps_target: Math.min(Math.max((plan.steps_target ?? 10000) + d * 2000, 6000), 12000) })} />
          <Stepper label="Calorie budget" value={String(plan.kcal)} onDelta={(d) => onPatch({ kcal_override: Math.min(Math.max(plan.kcal + d * 50, 1200), 4000) })} />
          <Stepper label="Water target" value={`${plan.water_ml} mL`} onDelta={(d) => onPatch({ water_override: Math.min(Math.max(plan.water_ml + d * 100, 1500), 5000) })} />
        </View>

        <Text style={[s.monoLabel, { marginTop: 20 }]}>CONNECTIONS</Text>
        <Pressable onPress={onConnectHealth} style={[s.stepRow, { marginTop: 8 }]}>
          <Text style={s.stepLabel}>♥  Apple Health</Text>
          <Text style={{ color: state.activity ? C.mint : C.dim, fontFamily: "JetBrainsMono_400Regular", fontSize: 11, letterSpacing: 1 }}>
            {state.activity ? "CONNECTED" : "CONNECT"}
          </Text>
        </Pressable>

        <Pressable
          onPress={() =>
            Alert.alert("Restart onboarding?", "Your plan resets. Meals stay.", [
              { text: "Cancel", style: "cancel" },
              { text: "Restart", style: "destructive", onPress: onRestart },
            ])
          }
          style={[s.stepRow, { marginTop: 16, justifyContent: "center" }]}
        >
          <Text style={{ color: C.body, fontWeight: "600" }}>Restart onboarding</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1, backgroundColor: C.ground },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 18,
    paddingTop: 8,
  },
  brand: { fontFamily: "InstrumentSerif_400Regular", fontSize: 24, color: C.ink },
  dayChip: {
    fontFamily: "JetBrainsMono_400Regular",
    fontSize: 10,
    letterSpacing: 2,
    color: C.mint,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
    overflow: "hidden",
  },
  personBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.card,
    alignItems: "center",
    justifyContent: "center",
  },
  dayCell: {
    width: 44,
    alignItems: "center",
    paddingVertical: 8,
    borderRadius: 12,
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.08)",
  },
  dayCellToday: { backgroundColor: "#221B3A", borderColor: "rgba(199,184,255,0.45)" },
  dayLetter: { fontFamily: "JetBrainsMono_400Regular", fontSize: 8, color: C.dim },
  dayNum: { fontFamily: "JetBrainsMono_400Regular", fontSize: 13, color: C.body, marginTop: 2 },
  dayDot: { width: 10, height: 2, borderRadius: 1, backgroundColor: C.lavDeep, marginTop: 4 },
  panel: { width: Dimensions.get("window").width, paddingHorizontal: 16 },
  heroCard: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 20,
    borderWidth: 1,
    borderColor: C.line,
    padding: 18,
  },
  heroBig: { fontFamily: "InstrumentSerif_400Regular", fontSize: 46, lineHeight: 50, color: C.ink },
  monoLabel: { fontFamily: "JetBrainsMono_400Regular", fontSize: 10, letterSpacing: 1.8, color: C.dim },
  ringPct: { fontFamily: "InstrumentSerif_400Regular", fontSize: 16, color: C.ink },
  trioRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  trioCard: {
    flex: 1,
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    borderRadius: 16,
    padding: 12,
  },
  trioValue: { fontFamily: "InstrumentSerif_400Regular", fontSize: 20, color: C.ink },
  trioLabel: { fontFamily: "JetBrainsMono_400Regular", fontSize: 8.5, letterSpacing: 1.4, color: C.dim, marginTop: 3 },
  healthCard: { borderRadius: 18, borderWidth: 1, borderColor: C.line, padding: 16, marginTop: 10 },
  healthTitle: { fontFamily: "InstrumentSerif_400Regular", fontSize: 17, color: C.ink },
  healthValue: { fontFamily: "InstrumentSerif_400Regular", fontSize: 19, color: C.lav },
  healthTrack: { height: 4, borderRadius: 2, backgroundColor: "rgba(199,184,255,0.14)", marginTop: 12, overflow: "hidden" },
  healthNote: { color: C.dim, fontSize: 12.5, marginTop: 10, lineHeight: 17 },
  smallBtn: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 10,
    paddingVertical: 8,
    alignItems: "center",
  },
  smallBtnText: { color: C.body, fontSize: 13, fontWeight: "600" },
  smallBtnLav: { marginTop: 12, backgroundColor: "#C3B4FF", borderRadius: 999, paddingVertical: 8, paddingHorizontal: 22 },
  smallBtnLavText: { color: "#17131F", fontSize: 13, fontWeight: "600" },
  dots: { flexDirection: "row", justifyContent: "center", gap: 6, marginTop: 12, alignItems: "center" },
  dot: { width: 5, height: 5, borderRadius: 3, backgroundColor: "rgba(199,184,255,0.25)" },
  dotActive: { width: 18, height: 5, backgroundColor: C.lavDeep },
  mealRow: {
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 11,
  },
  mealTile: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: "#2A2050",
    alignItems: "center",
    justifyContent: "center",
    marginRight: 12,
  },
  mealTileText: { fontFamily: "InstrumentSerif_400Regular", fontSize: 16, color: C.lav },
  mealName: { color: C.ink, fontSize: 14.5, fontWeight: "500" },
  mealMacros: { fontFamily: "JetBrainsMono_400Regular", fontSize: 10.5, color: C.dim, marginTop: 3 },
  mealTime: { fontFamily: "JetBrainsMono_400Regular", fontSize: 10.5, color: C.dim },
  fixLink: { fontFamily: "JetBrainsMono_400Regular", fontSize: 10, letterSpacing: 1.6, color: C.lav, paddingTop: 10 },
  ctaBar: {
    position: "absolute",
    left: 16,
    right: 16,
    bottom: 18,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  gbtn: { borderRadius: 999, paddingVertical: 15, alignItems: "center" },
  gbtnText: { color: "#17131F", fontSize: 15.5, fontWeight: "600" },
  scanBtn: {
    width: 48,
    height: 48,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.card,
    alignItems: "center",
    justifyContent: "center",
  },
  toast: {
    position: "absolute",
    left: 16,
    right: 16,
    bottom: 78,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(27,21,48,0.96)",
    borderWidth: 1,
    borderColor: C.line,
    borderRadius: 999,
    paddingHorizontal: 16,
    paddingVertical: 11,
  },
  // onboarding
  obProgress: { flexDirection: "row", gap: 6, paddingHorizontal: 18, paddingTop: 10 },
  obSeg: { flex: 1, height: 2.5, borderRadius: 2, backgroundColor: "rgba(199,184,255,0.15)" },
  obSegOn: { backgroundColor: C.lav },
  obBrand: { fontFamily: "InstrumentSerif_400Regular", fontSize: 40, color: C.ink, marginTop: 26 },
  obTagline: { color: C.body, fontSize: 15, lineHeight: 22, textAlign: "center", marginTop: 12, maxWidth: 280 },
  obBody: { flex: 1, paddingHorizontal: 20, paddingTop: 120 },
  obTitle: { fontFamily: "InstrumentSerif_400Regular", fontSize: 32, color: C.ink },
  obSub: { color: C.dim, fontSize: 13.5, lineHeight: 19, marginTop: 6, maxWidth: 300 },
  obEpigraph: {
    fontFamily: "InstrumentSerif_400Regular",
    fontStyle: "italic",
    color: C.dim,
    fontSize: 13.5,
    textAlign: "center",
    marginTop: 16,
  },
  obFooter: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingHorizontal: 18,
    paddingBottom: 24,
  },
  backBtn: {
    width: 46,
    height: 46,
    borderRadius: 23,
    borderWidth: 1,
    borderColor: C.line,
    backgroundColor: C.card,
    alignItems: "center",
    justifyContent: "center",
  },
  stepRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
    borderRadius: 14,
    paddingHorizontal: 16,
    paddingVertical: 13,
  },
  stepLabel: { color: C.body, fontSize: 14 },
  stepValue: { fontFamily: "InstrumentSerif_400Regular", fontSize: 18, color: C.ink },
  stepBtn: {
    width: 30,
    height: 30,
    borderRadius: 15,
    borderWidth: 1,
    borderColor: C.line,
    alignItems: "center",
    justifyContent: "center",
  },
  stepBtnPlus: { backgroundColor: "#2A2050" },
  stepBtnText: { color: C.lav, fontSize: 16, lineHeight: 18 },
  chip: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 11,
    borderRadius: 12,
    backgroundColor: C.card,
    borderWidth: 1,
    borderColor: "rgba(199,184,255,0.10)",
  },
  chipOn: { backgroundColor: "#2A2050", borderColor: "rgba(199,184,255,0.5)" },
  chipText: { color: C.body, fontSize: 13.5, fontWeight: "600" },
  summaryKcal: { fontFamily: "InstrumentSerif_400Regular", fontSize: 34, color: C.lav },
});
