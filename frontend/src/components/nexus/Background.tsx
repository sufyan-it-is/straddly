import React, { useEffect, useRef } from 'react';

const Background: React.FC = () => {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        let width: number, height: number;
        let particles: any[] = [];

        const resize = () => {
            width = window.innerWidth;
            height = window.innerHeight;
            canvas.width = width;
            canvas.height = height;
            init();
        };

        const init = () => {
            particles = [];
            for (let i = 0; i < 150; i++) {
                particles.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    size: Math.random() * 2 + 1,
                    speed: Math.random() * 0.5 + 0.2,
                    opacity: Math.random() * 0.5 + 0.2
                });
            }
        };

        const animate = () => {
            ctx.clearRect(0, 0, width, height);
            const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#adff2f';
            ctx.fillStyle = accent;

            particles.forEach(p => {
                p.y += p.speed;
                if (p.y > height) p.y = -10;
                ctx.globalAlpha = p.opacity;
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                ctx.fill();
            });
            requestAnimationFrame(animate);
        };

        window.addEventListener('resize', resize);
        resize();
        animate();

        return () => window.removeEventListener('resize', resize);
    }, []);

    return (
        <div className="nexus-bg-container">
            <canvas ref={canvasRef} />
            <div className="nexus-reflective-floor"></div>
            <div className="nexus-hud-overlay"></div>
            <div className="nexus-volumetric-light"></div>
            <style>{`
                .nexus-bg-container {
                    position: fixed;
                    top: 0; left: 0; width: 100%; height: 100%; z-index: -2;
                    background: #020617; overflow: hidden;
                }
                .nexus-bg-container canvas {
                    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                }
                .nexus-reflective-floor {
                    position: absolute; bottom: 0; width: 100%; height: 40vh;
                    background: linear-gradient(to top, rgba(15, 23, 42, 0.8) 0%, transparent 100%);
                    z-index: 3; border-top: 1px solid rgba(255, 255, 255, 0.05);
                }
                .nexus-hud-overlay {
                    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
                    background-image: linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
                                      linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
                    background-size: 50px 50px; z-index: 0; opacity: 0.5;
                    mask-image: radial-gradient(circle at center, black, transparent 80%);
                }
                .nexus-volumetric-light {
                    position: absolute; top: -20%; left: 50%; transform: translateX(-50%);
                    width: 150vw; height: 100vh;
                    background: radial-gradient(ellipse at center, rgba(56, 189, 248, 0.15) 0%, transparent 70%);
                    z-index: -1; pointer-events: none;
                }
            `}</style>
        </div>
    );
};

export default Background;
