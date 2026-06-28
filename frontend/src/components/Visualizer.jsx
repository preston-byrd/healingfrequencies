import React, { useEffect, useRef } from 'react';
import audioEngine from '@/lib/audioEngine';

/**
 * Multi-mode animated visualizer.
 *
 *   - rings   : original concentric undulating rings (default)
 *   - chladni : real-time Chladni nodal-pattern simulation. Sand particles
 *               drift toward the zeroes of the (n,m) standing-wave equation
 *               u(x,y) = cos(nπx)cos(mπy) − cos(mπx)cos(nπy). n,m are
 *               selected from the current frequency so different tones
 *               produce distinct sacred-geometry patterns.
 *   - ripples : fluid ripple emitter — concentric expanding waves whose
 *               birth rate is locked to the audio amplitude (via the engine
 *               AnalyserNode), so every beat of the isochronic pulse births
 *               a new ring. Calm, hypnotic.
 *
 * All three modes read live amplitude from `audioEngine.getAmplitude()` so
 * the visuals move in sync with what the user actually hears.
 */
export default function Visualizer({ playing, frequency, mode = 'rings' }) {
  const canvasRef = useRef(null);
  const rafRef = useRef(null);
  const tRef = useRef(0);
  // Particle state for Chladni mode. Lazily initialised on first frame in
  // that mode so we don't pay the cost when the user never switches to it.
  const particlesRef = useRef(null);
  // Ripple state — list of {birth, intensity} entries pruned per frame.
  const ripplesRef = useRef([]);
  const lastRippleRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // ---------- Chladni helpers --------------------------------------------
    const initParticles = (count = 900) => {
      const arr = new Array(count);
      for (let i = 0; i < count; i++) {
        arr[i] = { x: Math.random(), y: Math.random() };
      }
      particlesRef.current = arr;
    };
    // Mode parameters (n,m) derived from frequency. Different frequencies
    // produce visually distinct patterns; clamp to keep things readable.
    const chladniMode = (f) => {
      const k = Math.max(2, Math.min(7, 2 + Math.floor(Math.log2(Math.max(1, f) / 50))));
      // Asymmetric m so we get the classic non-degenerate Chladni figure
      return { n: k, m: k + 1 + ((Math.round(f) % 3)) };
    };
    const chladniU = (n, m, x, y, phase) => {
      // x,y in [0,1]. Add a tiny phase so the pattern slowly "breathes".
      const PI = Math.PI;
      return (
        Math.cos(n * PI * x + phase * 0.04) * Math.cos(m * PI * y) -
        Math.cos(m * PI * x) * Math.cos(n * PI * y - phase * 0.04)
      );
    };

    const drawRings = (t, w, h, cx, cy, R, amp) => {
      // Original rings visualizer, kept verbatim.
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 2.2);
      grad.addColorStop(0, 'rgba(92,158,140,0.30)');
      grad.addColorStop(0.5, 'rgba(92,158,140,0.06)');
      grad.addColorStop(1, 'rgba(8,18,15,0)');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);

      const fmod = (frequency / 528);
      const rings = 6;
      for (let i = 0; i < rings; i++) {
        const phase = t + i * 0.7;
        const baseR = R * (0.5 + i * 0.13);
        ctx.beginPath();
        const segs = 220;
        for (let s = 0; s <= segs; s++) {
          const a = (s / segs) * Math.PI * 2;
          const wob =
            Math.sin(a * 4 + phase * 1.2) * (6 + amp * 18) +
            Math.sin(a * 7 - phase * 0.6) * 4 * fmod +
            Math.sin(a * 2 + phase) * 8;
          const r = baseR + wob * (playing ? 1 : 0.4);
          const x = cx + Math.cos(a) * r;
          const y = cy + Math.sin(a) * r;
          if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        const alpha = 0.10 + (rings - i) * 0.04 * (playing ? 1 : 0.6);
        ctx.strokeStyle = i % 2 === 0
          ? `rgba(114, 194, 172, ${alpha})`
          : `rgba(196, 166, 122, ${alpha * 0.7})`;
        ctx.lineWidth = 1.2 * dpr;
        ctx.stroke();
      }

      const pulse = playing ? 1 + Math.sin(t * 2) * 0.04 + amp * 0.15 : 1;
      const orbR = R * 0.32 * pulse;
      const og = ctx.createRadialGradient(cx, cy, 0, cx, cy, orbR);
      og.addColorStop(0, 'rgba(114,194,172,0.55)');
      og.addColorStop(0.6, 'rgba(92,158,140,0.18)');
      og.addColorStop(1, 'rgba(92,158,140,0)');
      ctx.fillStyle = og;
      ctx.beginPath();
      ctx.arc(cx, cy, orbR, 0, Math.PI * 2);
      ctx.fill();
    };

    const drawChladni = (t, w, h, cx, cy, R, amp) => {
      // Soft dark backdrop with the tiny "vibrating plate" boundary suggestion
      ctx.fillStyle = 'rgba(8,18,15,0.18)';
      ctx.fillRect(0, 0, w, h);

      if (!particlesRef.current) initParticles(900);
      const { n, m } = chladniMode(frequency);
      const phase = t * (1 + amp * 2.5);
      const plate = Math.min(w, h) * 0.78;
      const ox = cx - plate / 2;
      const oy = cy - plate / 2;

      // Step every particle toward nearby nodal lines (gradient descent on |u|).
      const eps = 0.004;
      const stepBase = 0.0035 + amp * 0.012; // amp couples energy → motion
      const jitter = 0.0009;
      const ps = particlesRef.current;
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        const u = chladniU(n, m, p.x, p.y, phase);
        const uX = chladniU(n, m, p.x + eps, p.y, phase);
        const uY = chladniU(n, m, p.x, p.y + eps, phase);
        const dx = (uX - u) / eps;
        const dy = (uY - u) / eps;
        // Move toward zero crossing (descent on |u|).
        const sign = u >= 0 ? 1 : -1;
        const len = Math.hypot(dx, dy) + 1e-6;
        p.x -= sign * stepBase * (dx / len);
        p.y -= sign * stepBase * (dy / len);
        // Light Brownian jitter so particles don't fully freeze on nodal lines
        p.x += (Math.random() - 0.5) * jitter;
        p.y += (Math.random() - 0.5) * jitter;
        // Wrap-around (keep all particles on the plate)
        if (p.x < 0) p.x += 1; else if (p.x > 1) p.x -= 1;
        if (p.y < 0) p.y += 1; else if (p.y > 1) p.y -= 1;
      }

      // Render particles as glowing sand — bright at zero-crossings.
      ctx.fillStyle = `rgba(196, 166, 122, ${0.55 + amp * 0.35})`;
      for (let i = 0; i < ps.length; i++) {
        const p = ps[i];
        const u = chladniU(n, m, p.x, p.y, phase);
        const intensity = 1 - Math.min(1, Math.abs(u));
        if (intensity < 0.45) continue; // skip dim particles (perf)
        const x = ox + p.x * plate;
        const y = oy + p.y * plate;
        const size = (1.2 + intensity * 1.6) * dpr;
        ctx.globalAlpha = 0.35 + intensity * 0.6;
        ctx.beginPath();
        ctx.arc(x, y, size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // Thin frame to suggest the "plate"
      ctx.strokeStyle = 'rgba(114,194,172,0.18)';
      ctx.lineWidth = 1 * dpr;
      ctx.strokeRect(ox, oy, plate, plate);

      // (n,m) label so users can see the mode shift with frequency
      ctx.fillStyle = 'rgba(138,154,146,0.55)';
      ctx.font = `${10 * dpr}px ui-monospace, monospace`;
      ctx.fillText(`MODE  n=${n}  m=${m}`, ox + 8 * dpr, oy + plate - 8 * dpr);
    };

    const drawRipples = (t, w, h, cx, cy, R, amp) => {
      // Subtle teal wash backdrop
      ctx.fillStyle = 'rgba(8,18,15,0.22)';
      ctx.fillRect(0, 0, w, h);

      // Emit a new ripple ~once per period of the perceived frequency (slow
      // for sub-bass, faster for higher Hz). Bias by amp so isochronic pulses
      // each spawn a visible ripple.
      const now = t;
      const interval = Math.max(0.18, Math.min(1.4, 12 / Math.max(1, frequency)));
      const ampTrigger = amp > 0.04 ? 0.5 : 1.0; // amp-driven extra ripple
      if (playing && now - lastRippleRef.current > interval * ampTrigger) {
        ripplesRef.current.push({ birth: now, intensity: 0.6 + amp * 0.6 });
        lastRippleRef.current = now;
      }
      // Prune old ripples
      ripplesRef.current = ripplesRef.current.filter((r) => now - r.birth < 5.2);

      // Draw each ripple
      const maxR = R * 2.0;
      ctx.lineWidth = 1.4 * dpr;
      ripplesRef.current.forEach((r) => {
        const age = now - r.birth;
        const progress = age / 5.2; // 0..1
        const radius = progress * maxR;
        const alpha = Math.max(0, (1 - progress) * r.intensity) * 0.7;
        ctx.beginPath();
        // Slight ellipse for "fluid" feel
        const ry = radius * (0.97 + Math.sin(now * 1.3 + r.birth) * 0.025);
        ctx.ellipse(cx, cy, radius, ry, 0, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(114,194,172,${alpha})`;
        ctx.stroke();
        ctx.beginPath();
        ctx.ellipse(cx, cy, radius * 0.985, ry * 0.985, 0, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(196,166,122,${alpha * 0.35})`;
        ctx.stroke();
      });

      // Soft center drop with a tiny pulse
      const pulse = 1 + Math.sin(now * 2) * 0.06 + amp * 0.25;
      const orbR = R * 0.18 * pulse;
      const og = ctx.createRadialGradient(cx, cy, 0, cx, cy, orbR);
      og.addColorStop(0, 'rgba(114,194,172,0.7)');
      og.addColorStop(0.7, 'rgba(92,158,140,0.18)');
      og.addColorStop(1, 'rgba(92,158,140,0)');
      ctx.fillStyle = og;
      ctx.beginPath();
      ctx.arc(cx, cy, orbR, 0, Math.PI * 2);
      ctx.fill();
    };

    const draw = () => {
      tRef.current += playing ? 0.012 : 0.004;
      const t = tRef.current;
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.34;
      const amp = audioEngine.getAmplitude ? audioEngine.getAmplitude() : 0;

      ctx.clearRect(0, 0, w, h);

      if (mode === 'chladni') drawChladni(t, w, h, cx, cy, R, amp);
      else if (mode === 'ripples') drawRipples(t, w, h, cx, cy, R, amp);
      else drawRings(t, w, h, cx, cy, R, amp);

      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(rafRef.current);
      ro.disconnect();
    };
  }, [playing, frequency, mode]);

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" data-testid={`visualizer-${mode}`} />;
}
