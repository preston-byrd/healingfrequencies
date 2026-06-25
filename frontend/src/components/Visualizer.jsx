import React, { useEffect, useRef } from 'react';
import audioEngine from '@/lib/audioEngine';

// Animated canvas visualizer: concentric rings + flowing waves modulated by frequency.
export default function Visualizer({ playing, frequency }) {
  const canvasRef = useRef(null);
  const rafRef = useRef(null);
  const tRef = useRef(0);

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

    const draw = () => {
      tRef.current += playing ? 0.012 : 0.004;
      const t = tRef.current;
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.34;

      ctx.clearRect(0, 0, w, h);

      // Radial soft glow
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 2.2);
      grad.addColorStop(0, 'rgba(92,158,140,0.30)');
      grad.addColorStop(0.5, 'rgba(92,158,140,0.06)');
      grad.addColorStop(1, 'rgba(8,18,15,0)');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);

      // Concentric undulating rings
      const fmod = (frequency / 528); // normalized
      const rings = 6;
      for (let i = 0; i < rings; i++) {
        const phase = t + i * 0.7;
        const baseR = R * (0.5 + i * 0.13);
        ctx.beginPath();
        const segs = 220;
        for (let s = 0; s <= segs; s++) {
          const a = (s / segs) * Math.PI * 2;
          const wob =
            Math.sin(a * 4 + phase * 1.2) * 6 +
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

      // Center orb
      const pulse = playing ? 1 + Math.sin(t * 2) * 0.04 : 1;
      const orbR = R * 0.32 * pulse;
      const og = ctx.createRadialGradient(cx, cy, 0, cx, cy, orbR);
      og.addColorStop(0, 'rgba(114,194,172,0.55)');
      og.addColorStop(0.6, 'rgba(92,158,140,0.18)');
      og.addColorStop(1, 'rgba(92,158,140,0)');
      ctx.fillStyle = og;
      ctx.beginPath();
      ctx.arc(cx, cy, orbR, 0, Math.PI * 2);
      ctx.fill();

      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(rafRef.current);
      ro.disconnect();
    };
  }, [playing, frequency]);

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />;
}
