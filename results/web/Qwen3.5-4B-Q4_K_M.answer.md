# web-landing — réponse brute

## Consigne
Build a single-file landing page for a fictional product: "Nocturne", an app that helps people fall asleep faster by generating personalised evening routines.

Hard constraints:
- ONE .html file. All CSS in a <style> tag, all JS in a <script> tag. No external file, no CDN, no framework, no web font, no image URL.
- Must start with <!doctype html> and end with </html>. The file MUST be complete: never stop before </html>.
- Responsive: usable at 375px wide and at 1440px.
- Include: a hero section with a headline and a call-to-action button, exactly 3 feature cards, and at least one genuinely interactive element in vanilla JS (e.g. a working toggle, tab switcher, or form with validation).
- Stay compact: aim for 250-350 lines total. Prefer dense, idiomatic CSS over repetition and over-commented code.

Quality bar: this page will be judged side by side against other models on visual design only. Make deliberate choices: a coherent palette, a real type scale, spacing that breathes, a clear visual hierarchy, tasteful hover/focus states. Avoid generic bootstrap-looking output.

Return ONLY the HTML code. No markdown fence, no explanation, no comments about your choices.

## Réponse brute
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Nocturne | Sleep Faster</title>
    <style>
        :root {
            --bg: #0f1115;
            --surface: #16191f;
            --surface-hover: #1e2229;
            --primary: #7d5fff;
            --primary-glow: rgba(125, 95, 255, 0.4);
            --text: #e0e0e0;
            --text-muted: #888;
            --radius: 16px;
            --font: system-ui, -apple-system, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: var(--font);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            overflow-x: hidden;
        }
        .container { max-width: 1100px; margin: 0 auto; padding: 0 20px; }
        
        /* Hero */
        header {
            padding: 120px 0 80px;
            text-align: center;
            position: relative;
        }
        h1 {
            font-size: clamp(2.5rem, 5vw, 4.5rem);
            font-weight: 800;
            letter-spacing: -0.02em;
            margin-bottom: 16px;
            background: linear-gradient(to bottom right, #fff, #aaa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle {
            font-size: 1.25rem;
            color: var(--text-muted);
            max-width: 600px;
            margin: 0 auto 40px;
        }
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: var(--primary);
            color: #fff;
            padding: 14px 32px;
            border-radius: 50px;
            font-weight: 600;
            text-decoration: none;
            transition: transform 0.2s, box-shadow 0.2s;
            border: none;
            cursor: pointer;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px var(--primary-glow);
        }
        .btn:active { transform: translateY(0); }

        /* Features */
        .features { padding: 80px 0; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 24px;
        }
        .card {
            background: var(--surface);
            border: 1px solid rgba(255,255,255,0.05);
            border-radius: var(--radius);
            padding: 32px;
            transition: transform 0.3s ease, border-color 0.3s;
        }
        .card:hover {
            transform: translateY(-5px);
            border-color: var(--primary);
        }
        .icon {
            width: 48px;
            height: 48px;
            background: rgba(125, 95, 255, 0.1);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 20px;
            color: var(--primary);
        }
        .card h3 { font-size: 1.25rem; margin-bottom: 12px; }
        .card p { color: var(--text-muted); font-size: 0.95rem; }

        /* Interactive Section */
        .interactive {
            padding: 80px 0;
            background: linear-gradient(180deg, transparent, rgba(125, 95, 255, 0.05));
        }
        .interactive h2 { text-align: center; margin-bottom: 40px; font-size: 2rem; }
        .demo-panel {
            background: var(--surface);
            border-radius: var(--radius);
            padding: 40px;
            border: 1px solid rgba(255,255,255,0.05);
            max-width: 600px;
            margin: 0 auto;
        }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-size: 0.9rem; color: var(--text-muted); }
        input, select {
            width: 100%;
            padding: 12px;
            background: var(--bg);
            border: 1px solid #333;
            border-radius: 8px;
            color: #fff;
            font-size: 1rem;
            transition: border-color 0.2s;
        }
        input:focus, select:focus {
            outline: none;
            border-color: var(--primary);
        }
        .toggle-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
        }
        .switch {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 28px;
        }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #333;
            transition: .4s;
            border-radius: 34px;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 20px;
            width: 20px;
            left: 4px;
            bottom: 4px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }
        input:checked + .slider { background-color: var(--primary); }
        input:checked + .slider:before { transform: translateX(22px); }
        
        .status {
            margin-top: 20px;
            padding: 12px;
            border-radius: 8px;
            background: rgba(125, 95, 255, 0.1);
            border: 1px solid rgba(125, 95, 255, 0.2);
            display: none;
            text-align: center;
        }
        .status.show { display: block; animation: fadeIn 0.5s; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

        /* Footer */
        footer { padding: 40px 0; text-align: center; color: var(--text-muted); font-size: 0.9rem; border-top: 1px solid rgba(255,255,255,0.05); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Nocturne</h1>
            <p class="subtitle">Your personal sleep architect. We build the perfect evening routine to help you drift off instantly.</p>
            <button class="btn" onclick="document.getElementById('demo').scrollIntoView({behavior: 'smooth'})">
                Start Your Routine
            </button>
        </header>

        <section class="features">
            <div class="grid">
                <div class="card">
                    <div class="icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    </div>
                    <h3>Biological Timing</h3>
                    <p>We analyze your circadian rhythm to schedule wind-down activities exactly when your brain is primed for sleep.</p>
                </div>
                <div class="card">
                    <div class="icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
                    </div>
                    <h3>Customizable</h3>
                    <p>From meditation to reading, you define the steps. Nocturne adapts the duration based on your current stress levels.</p>
                </div>
                <div class="card">
                    <div class="icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                    </div>
                    <h3>Instant Sleep</h3>
                    <p>Our proprietary algorithm reduces sleep latency by 40% by ensuring your environment is perfectly optimized.</p>
                </div>
            </div>
        </section>

        <section class="interactive" id="demo">
            <div class="container">
                <h2>Try the Generator</h2>
                <div class="demo-panel">
                    <div class="form-group">
                        <label>Current Mood</label>
                        <select id="mood">
                            <option value="stressed">Stressed</option>
                            <option value="tired">Tired</option>
                            <option value="anxious">Anxious</option>
                            <option value="neutral">Neutral</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Preferred Activity</label>
                        <select id="activity">
                            <option value="meditation">Guided Meditation</option>
                            <option value="reading">Reading</option>
                            <option value="journaling">Journaling</option>
                        </select>
                    </div>
                    <div class="toggle-row">
                        <span>Enable Ambient Sound</span>
                        <label class="switch">
                            <input type="checkbox" id="sound">
                            <span class="slider"></span>
                        </label>
                    </div>
                    <button class="btn" style="width: 100%; justify-content: center;" onclick="generateRoutine()">Generate Routine</button>
                    <div id="status" class="status"></div>
                </div>
            </div>
        </section>

        <footer>
            <p>&copy; 2023 Nocturne Inc. All rights reserved.</p>
        </footer>
    </div>

    <script>
        function generateRoutine() {
            const mood = document.getElementById('mood').value;
            const activity = document.getElementById('activity').value;
            const sound = document.getElementById('sound').checked;
            const status = document.getElementById('status');
            
            let routine = [];
            let duration = 15;

            if (mood === 'stressed') {
                routine.push("Deep breathing exercises");
                duration = 20;
            } else if (mood === 'anxious') {
                routine.push("Progressive muscle relaxation");
                duration = 25;
            } else {
                routine.push("Light stretching");
                duration = 10;
            }

            routine.push(activity);
            if (sound) routine.push("Rain sounds playlist");

            const steps = routine.join(" &bull; ");
            status.innerHTML = `
                <strong>Generated Routine:</strong><br>
                ${steps}<br>
                <span style="font-size: 0.85em; opacity: 0.8">Estimated time: ${duration} mins</span>
            `;
            status.classList.add('show');
        }
    </script>
</body>
</html>
