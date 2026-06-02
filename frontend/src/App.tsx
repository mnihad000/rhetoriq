import { useEffect, useRef, useState } from "react";

const DENSITY = " .:-=+*#%@";
const GLITCH_CHARS = ["#", "@", "%", "&", "*", "+", "x", "0"];

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export default function App() {
  const heroRef = useRef<HTMLElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const grainCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<number | null>(null);
  const scrollProgressRef = useRef(0);
  const timeRef = useRef(0);
  const [scrollProgress, setScrollProgress] = useState(0);

  useEffect(() => {
    const updateProgress = () => {
      const hero = heroRef.current;
      if (!hero) {
        return;
      }

      const rect = hero.getBoundingClientRect();
      const travel = Math.max(hero.offsetHeight - window.innerHeight, 1);
      const progress = clamp(-rect.top / travel, 0, 1);

      scrollProgressRef.current = progress;
      setScrollProgress(progress);
    };

    updateProgress();
    window.addEventListener("scroll", updateProgress, { passive: true });
    window.addEventListener("resize", updateProgress);

    return () => {
      window.removeEventListener("scroll", updateProgress);
      window.removeEventListener("resize", updateProgress);
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const grainCanvas = grainCanvasRef.current;
    const ctx = canvas?.getContext("2d");
    const grainCtx = grainCanvas?.getContext("2d");

    if (!canvas || !grainCanvas || !ctx || !grainCtx) {
      return;
    }

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      grainCanvas.width = window.innerWidth;
      grainCanvas.height = window.innerHeight;
    };

    const generateFilmGrain = (
      width: number,
      height: number,
      intensity: number
    ) => {
      const imageData = grainCtx.createImageData(width, height);
      const { data } = imageData;

      for (let index = 0; index < data.length; index += 4) {
        const grain = (Math.random() - 0.5) * intensity * 255;
        const value = clamp(128 + grain, 0, 255);
        data[index] = value;
        data[index + 1] = value;
        data[index + 2] = value;
        data[index + 3] = Math.abs(grain) * 2.6;
      }

      return imageData;
    };

    const drawGlitchedOrb = (
      centerX: number,
      centerY: number,
      radius: number,
      hue: number,
      glitchIntensity: number
    ) => {
      ctx.save();

      const shouldGlitch =
        glitchIntensity > 0.52 && Math.random() < 0.12 + glitchIntensity * 0.1;
      const glitchOffset = shouldGlitch
        ? (Math.random() - 0.5) * 20 * glitchIntensity
        : 0;
      const glitchScale = shouldGlitch
        ? 1 + (Math.random() - 0.5) * 0.2 * glitchIntensity
        : 1;

      if (shouldGlitch) {
        ctx.translate(glitchOffset, glitchOffset * 0.6);
        ctx.scale(glitchScale, 1 / glitchScale);
      }

      const orbGradient = ctx.createRadialGradient(
        centerX,
        centerY,
        0,
        centerX,
        centerY,
        radius * 1.55
      );

      orbGradient.addColorStop(0, `hsla(${hue + 18}, 100%, 96%, 0.92)`);
      orbGradient.addColorStop(0.2, `hsla(${hue + 25}, 90%, 84%, 0.72)`);
      orbGradient.addColorStop(0.5, `hsla(${hue}, 76%, 55%, 0.38)`);
      orbGradient.addColorStop(1, "rgba(0, 0, 0, 0)");

      ctx.fillStyle = orbGradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const centerRadius = radius * 0.3;
      ctx.fillStyle = `hsla(${hue + 24}, 100%, 95%, 0.86)`;
      ctx.beginPath();
      ctx.arc(centerX, centerY, centerRadius, 0, Math.PI * 2);
      ctx.fill();

      if (shouldGlitch) {
        ctx.globalCompositeOperation = "screen";

        ctx.fillStyle = `hsla(94, 100%, 54%, ${0.45 * glitchIntensity})`;
        ctx.beginPath();
        ctx.arc(centerX + glitchOffset * 0.55, centerY, centerRadius, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = `hsla(228, 100%, 56%, ${0.42 * glitchIntensity})`;
        ctx.beginPath();
        ctx.arc(centerX - glitchOffset * 0.55, centerY, centerRadius, 0, Math.PI * 2);
        ctx.fill();

        ctx.globalCompositeOperation = "source-over";
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.52 * glitchIntensity})`;
        ctx.lineWidth = 1;

        for (let line = 0; line < 4; line += 1) {
          const y = centerY - radius + Math.random() * radius * 2;
          const startX = centerX - radius + Math.random() * 18;
          const endX = centerX + radius - Math.random() * 18;

          ctx.beginPath();
          ctx.moveTo(startX, y);
          ctx.lineTo(endX, y);
          ctx.stroke();
        }

        ctx.fillStyle = `rgba(255, 32, 214, ${0.3 * glitchIntensity})`;
        for (let block = 0; block < 3; block += 1) {
          const blockX = centerX - radius + Math.random() * radius * 2;
          const blockY = centerY - radius + Math.random() * radius * 2;
          const size = Math.random() * 10 + 2;
          ctx.fillRect(blockX, blockY, size, size);
        }
      }

      ctx.strokeStyle = `hsla(${hue + 20}, 90%, 76%, 0.54)`;
      ctx.lineWidth = 2;

      if (shouldGlitch) {
        const segments = 8;

        for (let segment = 0; segment < segments; segment += 1) {
          const startAngle = (segment / segments) * Math.PI * 2;
          const endAngle = ((segment + 1) / segments) * Math.PI * 2;
          const ringRadius =
            radius * 1.18 + (Math.random() - 0.5) * 10 * glitchIntensity;

          ctx.beginPath();
          ctx.arc(centerX, centerY, ringRadius, startAngle, endAngle);
          ctx.stroke();
        }
      } else {
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius * 1.18, 0, Math.PI * 2);
        ctx.stroke();
      }

      ctx.restore();
    };

    // Keep the hero self-animating and let scroll only shape the composition.
    const render = () => {
      timeRef.current += 0.016;
      const time = timeRef.current;
      const width = canvas.width;
      const height = canvas.height;
      const scroll = scrollProgressRef.current;

      if (!width || !height) {
        frameRef.current = window.requestAnimationFrame(render);
        return;
      }

      ctx.fillStyle = "#020406";
      ctx.fillRect(0, 0, width, height);

      const centerX = width / 2;
      const centerY = height * (0.46 - scroll * 0.06);
      const radius = Math.min(width, height) * 0.19;
      const hue = 176 + ((Math.sin(time * 0.72) + 1) / 2) * 56;
      const glitchSignal = Math.sin(time * 4.6);
      const glitchIntensity =
        glitchSignal > 0.94 ? 1 : glitchSignal > 0.74 ? 0.62 : 0.18;

      const bgGradient = ctx.createRadialGradient(
        centerX,
        centerY - 52,
        0,
        centerX,
        centerY,
        Math.max(width, height) * 0.82
      );

      bgGradient.addColorStop(0, `hsla(${hue + 36}, 88%, 62%, 0.33)`);
      bgGradient.addColorStop(0.35, `hsla(${hue}, 72%, 42%, 0.24)`);
      bgGradient.addColorStop(0.65, `hsla(${hue - 24}, 58%, 14%, 0.16)`);
      bgGradient.addColorStop(1, "rgba(1, 3, 5, 0.96)");

      ctx.fillStyle = bgGradient;
      ctx.fillRect(0, 0, width, height);

      drawGlitchedOrb(centerX, centerY, radius, hue, glitchIntensity);

      ctx.font = `${width < 768 ? 9 : 10}px "IBM Plex Mono", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const spacing = width < 768 ? 12 : 10;
      const cols = Math.min(Math.floor(width / spacing), 150);
      const rows = Math.min(Math.floor(height / spacing), 100);
      const rotation = time * 0.36;

      for (let column = 0; column < cols; column += 1) {
        for (let row = 0; row < rows; row += 1) {
          const x = (column - cols / 2) * spacing + centerX;
          const y = (row - rows / 2) * spacing + centerY;
          const dx = x - centerX;
          const dy = y - centerY;
          const distance = Math.sqrt(dx * dx + dy * dy);

          if (distance >= radius || Math.random() <= 0.4) {
            continue;
          }

          const z = Math.sqrt(Math.max(0, radius * radius - dx * dx - dy * dy));
          const rotatedZ = dx * Math.sin(rotation) + z * Math.cos(rotation);
          const brightness = (rotatedZ + radius) / (radius * 2);

          if (rotatedZ <= -radius * 0.3) {
            continue;
          }

          const densityIndex = Math.floor(brightness * (DENSITY.length - 1));
          let char = DENSITY[densityIndex];

          if (distance < radius * 0.8 && glitchIntensity > 0.8 && Math.random() < 0.3) {
            char = GLITCH_CHARS[Math.floor(Math.random() * GLITCH_CHARS.length)];
          }

          const alpha = clamp(brightness + scroll * 0.12, 0.24, 1);
          ctx.fillStyle = `rgba(242, 248, 255, ${alpha})`;
          ctx.fillText(char, x, y);
        }
      }

      grainCtx.clearRect(0, 0, width, height);
      const grainIntensity = 0.18 + Math.sin(time * 10) * 0.02 + scroll * 0.03;
      grainCtx.putImageData(generateFilmGrain(width, height, grainIntensity), 0, 0);

      grainCtx.globalCompositeOperation = "screen";
      for (let sparkle = 0; sparkle < 90; sparkle += 1) {
        const x = Math.random() * width;
        const y = Math.random() * height;
        const size = Math.random() * 2 + 0.4;
        const opacity = Math.random() * 0.2 + glitchIntensity * 0.08;

        grainCtx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
        grainCtx.beginPath();
        grainCtx.arc(x, y, size, 0, Math.PI * 2);
        grainCtx.fill();
      }

      grainCtx.globalCompositeOperation = "multiply";
      for (let shadow = 0; shadow < 40; shadow += 1) {
        const x = Math.random() * width;
        const y = Math.random() * height;
        const size = Math.random() * 1.4 + 0.4;
        const opacity = Math.random() * 0.42 + 0.4;

        grainCtx.fillStyle = `rgba(0, 0, 0, ${opacity})`;
        grainCtx.beginPath();
        grainCtx.arc(x, y, size, 0, Math.PI * 2);
        grainCtx.fill();
      }

      grainCtx.globalCompositeOperation = "source-over";
      frameRef.current = window.requestAnimationFrame(render);
    };

    resize();
    render();
    window.addEventListener("resize", resize);

    return () => {
      window.removeEventListener("resize", resize);
      if (frameRef.current) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);

  const overlayOpacity = Math.max(0, 1 - scrollProgress * 1.55);
  const titleShift = scrollProgress * 96;
  const leftShift = scrollProgress * -180;
  const rightShift = scrollProgress * 180;
  const metaShift = scrollProgress * 60;

  return (
    <main className="app-shell">
      <section ref={heroRef} className="hero-shell">
        <div className="hero-stage">
          <canvas ref={canvasRef} className="hero-canvas" />
          <canvas ref={grainCanvasRef} className="hero-grain" />
          <div className="hero-vignette" />

          <div className="hero-overlay">
            <nav className="hero-nav" style={{ opacity: Math.max(0.18, overlayOpacity) }}>
              <div className="hero-mark" aria-label="Rhetoriq logo">
                <span />
              </div>
              <div className="hero-links">
                <a href="#brief">Narrative Maps</a>
                <a href="#method">Method</a>
                <a href="#signal">Signals</a>
              </div>
              <a className="hero-cta" href="#brief">
                Request Access
              </a>
            </nav>

            <div
              className="hero-title-wrap"
              style={{
                opacity: overlayOpacity,
                transform: `translate(-50%, ${titleShift}px)`
              }}
            >
              <p className="hero-kicker">Narrative intelligence platform</p>
              <h1 className="hero-title">RHETORIQ</h1>
            </div>

            <div
              className="hero-side hero-side-left"
              style={{
                opacity: overlayOpacity,
                transform: `translateX(${leftShift}px)`
              }}
            >
              Track where news begins, who sharpens it, and how it hardens into belief.
            </div>

            <div
              className="hero-side hero-side-right"
              style={{
                opacity: overlayOpacity,
                transform: `translateX(${rightShift}px)`
              }}
            >
              Follow the jump from origin point to outlet, subreddit, and political stage.
            </div>

            <div
              className="hero-meta"
              style={{
                opacity: overlayOpacity,
                transform: `translateY(${metaShift}px)`
              }}
            >
              Signal tracing for narratives, amplification, and coordinated spread.
            </div>
          </div>
        </div>
      </section>

      <section id="brief" className="content-section">
        <div className="section-heading">
          <p className="section-kicker">Briefing surface</p>
          <h2>
            Understand how a phrase moves from fringe mention to mainstream repetition.
          </h2>
          <p className="section-copy">
            Rhetoriq turns narrative spread into something you can inspect: origin,
            velocity, amplification, and cross-platform pickup in one surface.
          </p>
        </div>

        <div className="insight-grid">
          <article id="signal" className="insight-card">
            <p className="card-index">01</p>
            <h3>Signal Detection</h3>
            <p>
              Surface sudden spikes in phrasing, framing, and coordinated talking
              points before they settle into the broader cycle.
            </p>
          </article>

          <article id="method" className="insight-card">
            <p className="card-index">02</p>
            <h3>Spread Mapping</h3>
            <p>
              Trace how a narrative jumps from source to source and identify the
              accounts, outlets, or communities accelerating it.
            </p>
          </article>

          <article className="insight-card">
            <p className="card-index">03</p>
            <h3>Investigation Output</h3>
            <p>
              Package each spread pattern into a readable brief with timestamps,
              key amplifiers, and a defensible timeline of adoption.
            </p>
          </article>
        </div>
      </section>
    </main>
  );
}
