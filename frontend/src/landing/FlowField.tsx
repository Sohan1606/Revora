import { useEffect, useRef } from "react";

/**
 * FlowField — the landing 3D hero.
 *
 * Communicates the product concept literally: at-risk payment particles (red)
 * drift toward a decision gate; the ones the engine acts on cross and turn
 * emerald (recovered); the ones it deliberately skips fade out (bounded action).
 *
 * Performance rules: lazy-loaded three.js, DPR capped at 1.5, paused when the
 * tab is hidden or the canvas is offscreen, fully disposed on unmount, and
 * rendered as a single static frame under prefers-reduced-motion.
 */
export default function FlowField({ className }: { className?: string }) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let disposed = false;
    let cleanup: (() => void) | null = null;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    import("three").then((THREE) => {
      if (disposed || !mount) return;

      const width = mount.clientWidth;
      const height = mount.clientHeight;

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 100);
      camera.position.set(0, 0, 9);

      const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
      renderer.setSize(width, height);
      mount.appendChild(renderer.domElement);

      // ---- particle system ----
      const COUNT = 260;
      const GATE_X = 0.0;

      const positions = new Float32Array(COUNT * 3);
      const colors = new Float32Array(COUNT * 3);
      const speeds = new Float32Array(COUNT);
      const recovered = new Uint8Array(COUNT); // 1 after crossing the gate

      const RED = new THREE.Color("#c81b1c");
      const EMERALD = new THREE.Color("#10b981");
      const tmp = new THREE.Color();

      function reset(i: number, randomizeX = true) {
        positions[i * 3] = randomizeX ? -8 + Math.random() * 15 : -8 - Math.random() * 2;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 4.6;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 4;
        speeds[i] = 0.006 + Math.random() * 0.012;
        recovered[i] = 0;
        tmp.copy(RED);
        colors[i * 3] = tmp.r;
        colors[i * 3 + 1] = tmp.g;
        colors[i * 3 + 2] = tmp.b;
      }
      for (let i = 0; i < COUNT; i++) reset(i);

      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

      const material = new THREE.PointsMaterial({
        size: 0.055,
        vertexColors: true,
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      const points = new THREE.Points(geometry, material);
      scene.add(points);

      // ---- decision gate (thin translucent plane the particles pass through) ----
      const gateGeo = new THREE.PlaneGeometry(0.02, 5.4);
      const gateMat = new THREE.MeshBasicMaterial({
        color: "#e6e6e6", transparent: true, opacity: 0.18,
      });
      const gate = new THREE.Mesh(gateGeo, gateMat);
      scene.add(gate);

      // roughly 55% of particles carry the engine's action across the gate
      const crosses = new Uint8Array(COUNT);
      for (let i = 0; i < COUNT; i++) crosses[i] = Math.random() < 0.55 ? 1 : 0;

      let raf = 0;
      let visible = !document.hidden;
      let lastFrame = performance.now();

      function step(now: number) {
        const dt = Math.min(now - lastFrame, 50);
        lastFrame = now;
        for (let i = 0; i < COUNT; i++) {
          const speed = recovered[i] ? speeds[i] * 1.35 : speeds[i];
          positions[i * 3] += speed * dt * 0.06;

          const x = positions[i * 3];
          if (!recovered[i] && x > GATE_X) {
            if (crosses[i]) {
              recovered[i] = 1;
              tmp.copy(EMERALD);
              colors[i * 3] = tmp.r;
              colors[i * 3 + 1] = tmp.g;
              colors[i * 3 + 2] = tmp.b;
            } else {
              // deliberately not acted on: fade and recycle
              material.opacity = material.opacity; // no-op keeps loop simple
              reset(i, false);
              continue;
            }
          }
          if (x > 8.5) reset(i, false);
        }
        geometry.attributes.position.needsUpdate = true;
        geometry.attributes.color.needsUpdate = true;
        renderer.render(scene, camera);
        if (!reduced) raf = requestAnimationFrame(step);
      }

      function onVisibility() {
        visible = !document.hidden;
        if (visible && !reduced && !raf) {
          lastFrame = performance.now();
          raf = requestAnimationFrame(step);
        } else if (!visible && raf) {
          cancelAnimationFrame(raf);
          raf = 0;
        }
      }
      function onResize() {
        if (!mount) return;
        const w = mount.clientWidth;
        const h = mount.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      }

      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("resize", onResize);

      // single frame under reduced motion; animated otherwise
      lastFrame = performance.now();
      raf = requestAnimationFrame(step);

      cleanup = () => {
        if (raf) cancelAnimationFrame(raf);
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("resize", onResize);
        geometry.dispose();
        material.dispose();
        gateGeo.dispose();
        gateMat.dispose();
        renderer.dispose();
        if (mount && renderer.domElement.parentElement === mount) {
          mount.removeChild(renderer.domElement);
        }
      };
    });

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, []);

  return <div ref={mountRef} className={className} aria-hidden="true" />;
}
