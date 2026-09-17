"use client";

import { useState, useEffect, useRef } from "react";

const SITE_NAME = "AUTO_REPLENISH";

export default function LoadingCover() {
  const [isVisible, setIsVisible] = useState(true);
  const [phase, setPhase] = useState("init");
  const [progress, setProgress] = useState(0);
  const [isHidden, setIsHidden] = useState(false);
  const rafRef = useRef<number | null>(null);
  const hasCompletedRef = useRef(false);
  const pageReadyRef = useRef(false);

  useEffect(() => {
    document.body.style.overflow = "hidden";

    const checkPageReady = () => {
      const mainEl = document.querySelector("main");
      if (mainEl && mainEl.textContent && mainEl.textContent.trim().length > 50) {
        pageReadyRef.current = true;
        return true;
      }
      const heading = document.querySelector("h1");
      if (heading && heading.textContent && heading.textContent.trim().length > 0) {
        pageReadyRef.current = true;
        return true;
      }
      return false;
    };

    const MIN_DURATION = 2000;
    const start = Date.now();

    const animateProgress = () => {
      const elapsed = Date.now() - start;

      let pct;
      if (pageReadyRef.current && elapsed > MIN_DURATION) {
        pct = 100;
      } else {
        pct = Math.min((elapsed / MIN_DURATION) * 85, 85);
      }
      setProgress(pct);

      if (pct >= 100 && !hasCompletedRef.current) {
        hasCompletedRef.current = true;
        setPhase("complete");

        setTimeout(() => {
          setPhase("sweeping");
          setTimeout(() => {
            document.body.style.overflow = "";
            setIsHidden(true);
            setTimeout(() => setIsVisible(false), 50);
          }, 1000);
        }, 500);
        return;
      }

      rafRef.current = requestAnimationFrame(animateProgress);
    };
    rafRef.current = requestAnimationFrame(animateProgress);

    const observer = new MutationObserver(() => {
      checkPageReady();
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    const pollTimer = setInterval(() => {
      checkPageReady();
    }, 500);

    const forceTimer = setTimeout(() => {
      pageReadyRef.current = true;
    }, 15000);

    checkPageReady();

    return () => {
      clearInterval(pollTimer);
      clearTimeout(forceTimer);
      observer.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      document.body.style.overflow = "";
    };
  }, []);

  if (!isVisible) return null;

  return (
    <div
      className={`fixed inset-0 z-[99999] flex items-center justify-center bg-[#0f1419]`}
      style={{
        opacity: isHidden ? 0 : 1,
        transition: "opacity 0.3s ease",
        pointerEvents: isHidden ? "none" : "auto",
      }}
    >
      <div
        className="sweep-overlay"
        style={{
          position: "absolute",
          inset: 0,
          background: "var(--endspace-accent)",
          zIndex: 100,
          pointerEvents: "none",
          transform: phase === "sweeping" ? "translateX(0)" : "translateX(-100%)",
          transition: phase === "sweeping" ? "transform 0.8s ease-in-out" : "none",
        }}
      />

      <div className="absolute left-0 top-0 h-full w-[8px] bg-white/10">
        <div
          className="absolute left-0 top-0 w-full transition-all duration-75"
          style={{
            height: `${progress}%`,
            background: "var(--endspace-accent)",
          }}
        />
      </div>

      <div className="relative z-10 text-center" style={{ fontFamily: "'JetBrains Mono', 'Consolas', monospace" }}>
        <div className="mb-4 text-5xl font-bold tracking-[0.15em] text-white">
          {SITE_NAME}
        </div>
        <div className="mb-2 text-2xl font-bold tracking-wider" style={{ color: "var(--endspace-accent)" }}>
          {Math.floor(progress)}%
        </div>
        <div className="flex items-center justify-center gap-2">
          <span
            className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ backgroundColor: "var(--endspace-accent)" }}
          />
          <span className="text-xs tracking-[0.2em] text-white/60">
            {phase === "sweeping" ? "LAUNCHING" : progress >= 100 ? "READY" : phase === "init" ? "INITIALIZING" : "LOADING"}
          </span>
        </div>
      </div>
    </div>
  );
}
