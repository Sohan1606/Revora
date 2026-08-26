import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { rupees } from "../lib/format";
import FlowField from "../landing/FlowField";

/* ------------------------------------------------------------------ *
 * Landing — MotionSites design language (fixed-stage hero, left-locked
 * type, choreographed entrance, sharp red CTAs, SG/JB type) merged with
 * a cinematic scroll and a 3D hero that visualizes the actual product.
 * Stats shown are REAL numbers from the labeled evaluation corpus —
 * never invented. No corpus → honest empty state.
 * ------------------------------------------------------------------ */

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260809_132544_b6ef0174-ed95-45ad-9a2f-ccb8acfbdce8.mp4";

const EXPO = "cubic-bezier(.16,1,.3,1)";
const QUINT = "cubic-bezier(.22,1,.36,1)";
const TYPE = "cubic-bezier(.22,.85,.24,1)";

function useEntrance() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return; // everything visible statically

    const anims: Animation[] = [];
    const t = (el: string | null) => (el ? root.querySelector<HTMLElement>(el) : null);

    const fadeUp = (el: HTMLElement | null, delay: number, dur: number, dy: number, ease: string) => {
      if (!el) return;
      el.style.opacity = "0";
      anims.push(
        el.animate(
          [{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: "translateY(0)" }],
          { duration: dur * 1000, delay: delay * 1000, easing: ease, fill: "both" }
        )
      );
    };
    const wipe = (el: HTMLElement | null, delay: number, dur: number) => {
      if (!el) return;
      anims.push(
        el.animate(
          [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0% 0 0)" }],
          { duration: dur * 1000, delay: delay * 1000, easing: EXPO, fill: "both" }
        )
      );
    };
    const riseLine = (el: HTMLElement | null, delay: number) => {
      if (!el) return;
      el.parentElement!.style.overflow = "hidden";
      anims.push(
        el.animate([{ transform: "translateY(120%)" }, { transform: "translateY(0)" }],
          { duration: 980, delay: delay * 1000, easing: TYPE, fill: "both" })
      );
    };

    // MotionSites timing table (adapted): wordmark → nav → headline → sub → CTA → stats
    fadeUp(t("[data-a=wordmark]"), 0.00, 0.70, 0, EXPO);
    t("[data-a=wordmark]")?.animate(
      [{ transform: "scale(.9)" }, { transform: "scale(1)" }],
      { duration: 700, easing: EXPO, fill: "both" });
    document.querySelectorAll<HTMLElement>("[data-a=nav]").forEach((el, i) => {
      fadeUp(el, 0.12 + i * 0.055, 0.62, 7, QUINT);
    });
    riseLine(t("[data-a=line1]"), 0.34);
    riseLine(t("[data-a=line2]"), 0.43);
    fadeUp(t("[data-a=sub]"), 0.74, 0.72, 14, QUINT);
    wipe(t("[data-a=cta]"), 0.90, 0.70);
    document.querySelectorAll<HTMLElement>("[data-a=stat]").forEach((el, i) => {
      fadeUp(el, 0.98 + i * 0.085, 0.62, 10, QUINT);
    });
    return () => anims.forEach((a) => a.cancel());
  }, []);
  return ref;
}

function useReveal() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const targets = Array.from(root.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    targets.forEach((el) => (el.style.opacity = "0"));
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            (entry.target as HTMLElement).animate(
              [{ opacity: 0, transform: "translateY(18px)" }, { opacity: 1, transform: "translateY(0)" }],
              { duration: 640, easing: QUINT, fill: "both" }
            );
            io.unobserve(entry.target);
          }
        }),
      { threshold: 0.2 }
    );
    targets.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
  return ref;
}

interface Evidence {
  data_label: string;
  has_data: boolean;
  metrics: { cases_total: number; revenue_at_risk_paise: number; recovered_paise: number } | null;
}

const LOOP = [
  ["Detect", "payment.failed webhooks become revenue-risk cases — signature-verified, idempotent."],
  ["Diagnose", "failure root cause classified with confidence; unknown stays unknown, never guessed."],
  ["Decide", "every candidate action scored: P(recovery) × amount − cost − friction. Doing nothing is a valid choice."],
  ["Gate", "deterministic policy rules override any model. Hard declines are never retried."],
  ["Execute", "bounded, idempotent actions — real Razorpay test-mode orders, labeled simulated messaging."],
  ["Prove", "treatment vs control measures incremental recovery. Not gross — incremental."],
];

export default function Landing() {
  const heroRef = useEntrance();
  const revealRef = useReveal();
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [videoOk, setVideoOk] = useState(true);

  useEffect(() => {
    api<Evidence>("/public/evidence")
      .then(setEvidence)
      .catch(() => setEvidence(null));
  }, []);

  const m = evidence?.metrics;

  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <style>{`
        .sg { font-family: 'Space Grotesk', system-ui, sans-serif; }
        .jb { font-family: 'JetBrains Mono', ui-monospace, monospace; }
      `}</style>

      {/* ============ HERO — fixed stage ============ */}
      <section ref={heroRef} className="relative h-screen overflow-hidden">
        {/* graded video plate */}
        <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
          <svg width="0" height="0" style={{ position: "absolute" }}>
            <filter id="lut" colorInterpolationFilters="sRGB">
              <feComponentTransfer>
                <feFuncR type="table" tableValues="0.002 0.015 0.031 0.072 0.147 0.249 0.347 0.476 0.605 0.720 0.839 0.930" />
                <feFuncG type="table" tableValues="0.002 0.016 0.033 0.071 0.121 0.203 0.305 0.383 0.462 0.601 0.751 0.930" />
                <feFuncB type="table" tableValues="0.002 0.019 0.047 0.092 0.131 0.202 0.295 0.394 0.476 0.581 0.732 0.930" />
              </feComponentTransfer>
            </filter>
          </svg>
          {videoOk && (
            <video
              src={VIDEO_URL}
              autoPlay muted loop playsInline preload="auto"
              onError={() => setVideoOk(false)}
              className="h-full w-full object-cover object-center opacity-40"
              style={{ filter: "url(#lut)" }}
            />
          )}
          {!videoOk && (
            <div className="h-full w-full bg-[radial-gradient(ellipse_at_70%_20%,rgba(200,27,28,0.16),transparent_60%)]" />
          )}
        </div>

        {/* 3D flow field — the product concept as atmosphere */}
        <FlowField className="absolute inset-0" />

        {/* scrim */}
        <div className="absolute inset-0 bg-gradient-to-r from-black/80 via-black/45 to-transparent" />

        {/* top bar */}
        <header className="absolute left-0 right-0 top-0 flex items-center justify-between px-[6vw] pt-8">
          <div data-a="wordmark" className="sg text-xl font-bold tracking-wide">
            REVO<span className="text-[#c81b1c]">RA</span>
          </div>
          <nav className="hidden items-center gap-8 jb text-[13px] text-zinc-300 md:flex">
            <a href="#loop" data-a="nav" className="hover:text-zinc-100">How it decides</a>
            <a href="#evidence" data-a="nav" className="hover:text-zinc-100">Evidence</a>
            <Link to="/console" data-a="nav" className="text-[#ff6b6b] hover:text-[#ff8f8f]">Open console</Link>
          </nav>
        </header>

        {/* left-locked type */}
        <div className="absolute left-[6vw] top-1/2 max-w-3xl -translate-y-1/2">
          <h1 className="sg text-[clamp(2.4rem,5.2vw,4.6rem)] font-bold leading-[1.06] tracking-tight">
            <span className="block"><span data-a="line1" className="block pb-1">Revenue recovery is</span></span>
            <span className="block"><span data-a="line2" className="block pb-1">a <span className="text-[#c81b1c]">decision</span> problem.</span></span>
          </h1>
          <p data-a="sub" className="jb mt-6 max-w-xl text-sm leading-relaxed text-zinc-300 md:text-[15px]">
            REVORA detects revenue at risk, diagnoses why, chooses the highest
            expected-value safe action — or deliberately does nothing — then
            proves the money it actually recovered.
          </p>
          <div className="mt-10 flex flex-wrap items-center gap-5">
            <Link
              to="/console"
              data-a="cta"
              className="sg inline-flex items-center gap-3 bg-[#c81b1c] px-7 py-4 text-[15px] font-medium text-white transition-colors hover:bg-[#a01516]"
            >
              Open the console <span aria-hidden>→</span>
            </Link>
            <a data-a="cta" href="#loop" className="jb text-[13px] text-zinc-400 underline-offset-4 hover:text-zinc-200 hover:underline">
              see how it decides
            </a>
          </div>
        </div>

        {/* stats strip — REAL corpus numbers or honest empty state */}
        <div className="absolute bottom-0 left-0 right-0 border-t border-white/10 bg-black/40 backdrop-blur-sm">
          <div className="flex flex-wrap items-center gap-x-12 gap-y-2 px-[6vw] py-4">
            <div data-a="stat">
              <div className="jb text-lg text-zinc-100">{m ? rupees(m.recovered_paise) : "—"}</div>
              <div className="sg text-[11px] uppercase tracking-wider text-zinc-500">Recovered (corpus)</div>
            </div>
            <div data-a="stat">
              <div className="jb text-lg text-zinc-100">{m ? rupees(m.revenue_at_risk_paise) : "—"}</div>
              <div className="sg text-[11px] uppercase tracking-wider text-zinc-500">At risk (corpus)</div>
            </div>
            <div data-a="stat">
              <div className="jb text-lg text-zinc-100">{m ? m.cases_total : "—"}</div>
              <div className="sg text-[11px] uppercase tracking-wider text-zinc-500">Cases evaluated</div>
            </div>
            <div data-a="stat" className="ml-auto max-w-xs text-right">
              <div className="jb text-[11px] text-amber-400/90">labeled synthetic evaluation corpus</div>
              <div className="sg text-[11px] text-zinc-600">live merchant metrics require sign-in</div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ THE LOOP ============ */}
      <section id="loop" ref={revealRef} className="border-t border-white/10 px-[6vw] py-24">
        <div data-reveal className="mb-14 max-w-2xl">
          <div className="jb text-[11px] uppercase tracking-[0.22em] text-zinc-500">The closed loop</div>
          <h2 className="sg mt-3 text-3xl font-bold tracking-tight md:text-4xl">
            Not a reminder bot.<br />A decision engine with constraints.
          </h2>
          <p className="jb mt-4 text-sm leading-relaxed text-zinc-400">
            AI recommends and optimizes; deterministic systems constrain and execute.
            Every stage below is implemented, tested, and leaves an audit trail.
          </p>
        </div>
        <div className="grid gap-px overflow-hidden border border-white/10 bg-white/10 md:grid-cols-3">
          {LOOP.map(([title, body], i) => (
            <div key={title} data-reveal className="bg-black p-7">
              <div className="jb text-[11px] text-[#c81b1c]">{String(i + 1).padStart(2, "0")}</div>
              <div className="sg mt-2 text-lg font-semibold">{title}</div>
              <div className="jb mt-2 text-[13px] leading-relaxed text-zinc-400">{body}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ============ EVIDENCE ============ */}
      <section id="evidence" className="border-t border-white/10 px-[6vw] py-24">
        <div className="grid items-start gap-14 md:grid-cols-2">
          <div>
            <div data-reveal className="jb text-[11px] uppercase tracking-[0.22em] text-zinc-500">Evidence, not claims</div>
            <h2 data-reveal className="sg mt-3 text-3xl font-bold tracking-tight md:text-4xl">
              Gross recovery is vanity.<br />Incremental is the truth.
            </h2>
            <p data-reveal className="jb mt-4 text-sm leading-relaxed text-zinc-400">
              Some customers pay anyway. REVORA runs treatment-vs-control
              experiments — its policy against a naive-dunning baseline — and
              reports the difference, from authoritative outcome rows only,
              with honest sample sizes.
            </p>
            <div data-reveal className="jb mt-6 space-y-2 text-[13px] text-zinc-500">
              <div>· held-out ML evaluation: AUC 0.70 vs logistic baseline — published in the repo</div>
              <div>· every decision stores all candidates, probabilities, costs and policy verdicts</div>
              <div>· synthetic data is labeled everywhere it appears — never blended with live metrics</div>
            </div>
          </div>
          <div data-reveal className="border border-white/10 bg-zinc-950 p-8">
            <div className="jb text-[11px] uppercase tracking-wider text-zinc-500">Decision evidence — one case</div>
            <div className="mono mt-4 space-y-1.5 text-[12.5px] leading-relaxed text-zinc-400">
              <div>amount at risk <span className="text-zinc-100">₹8,999</span> · cause <span className="text-zinc-100">insufficient_funds_temporary</span></div>
              <div className="pt-2 text-zinc-500">candidates evaluated:</div>
              <div><span className="text-emerald-400">wait 36h</span> · p=0.30 · EV ₹2,699</div>
              <div><span className="text-red-400">retry</span> · p=0.95 · EV ₹8.4L-scale <span className="text-red-500/70">· blocked: hard rule</span></div>
              <div><span className="text-zinc-300">message</span> · p=0.30 · EV ₹2,519</div>
              <div><span className="text-zinc-300">no_action</span> · EV ₹0 — always valid</div>
              <div className="pt-2">→ chosen <span className="text-zinc-100">retry via Razorpay test-mode order</span></div>
              <div>→ outcome <span className="text-emerald-400">recovered ₹8,999</span> · source webhook</div>
            </div>
            <div className="mt-5 text-[11px] text-zinc-600">
              Illustrative structure — real cases render this table live in the console with your data.
            </div>
          </div>
        </div>
      </section>

      {/* ============ FOOTER CTA ============ */}
      <section className="border-t border-white/10 px-[6vw] py-20 text-center">
        <h2 data-reveal className="sg text-3xl font-bold tracking-tight md:text-4xl">
          Your code speaks louder than your resume.
        </h2>
        <p data-reveal className="jb mx-auto mt-3 max-w-md text-sm text-zinc-500">
          Open the console, run a scenario in the Demo Lab, and watch a recovery
          case decide itself — with the audit trail to prove it.
        </p>
        <Link
          data-reveal
          to="/console"
          className="sg mt-8 inline-flex items-center gap-3 bg-[#c81b1c] px-8 py-4 text-[15px] font-medium text-white transition-colors hover:bg-[#a01516]"
        >
          Launch REVORA <span aria-hidden>→</span>
        </Link>
        <div className="jb mt-14 flex flex-col items-center gap-1 text-[11px] text-zinc-600">
          <div>REVORA — Revenue Recovery Decision Intelligence · Razorpay AI Buildathon, Track 03</div>
          <div>
            Honest by design: Razorpay Test Mode only · simulated messaging labeled ·
            synthetic data never mixed with live metrics
          </div>
        </div>
      </section>
    </div>
  );
}
