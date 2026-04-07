1. Visual Principles Behind the Image You Liked

That design likely uses these elements:

Dark base (not pure black)

Instead of #000, use pigment-like dark colors:

#0b0b0f   // ink black
#101014   // charcoal
#121018   // indigo black
#1a1620   // deep purple

This gives that “pigment / natural dye” feel.

Subtle Alpona / Mandala Line Art

White chalk-like patterns.

Color:

#dcd7c9
#e8e3d5
#f0ede4

Opacity:

0.05 – 0.12

This makes them look painted, not glowing.

Muted gold accents

Instead of bright yellow:

#c9a96a
#b89758
#9a7d44

Used very sparingly.

Soft audio glow (not particles)

Instead of bright dots:

blurred radial gradient

Example:

rgba(220,215,201,0.15)
2. Core UI Structure for a Voice Agent

Typical layout:

+---------------------------+
|                           |
|        mandala            |
|           ○               |
|       waveform ring       |
|                           |
|      Listening...         |
|                           |
+---------------------------+

Where:

center = mic / orb
ring = audio waveform
background = alpona patterns
3. Example Code (Canvas Animation)

Here’s a simple Indian-style voice orb animation.

What this creates:

mandala rings
breathing waveform circle
chalk-style colors

Much closer to the Image 1 vibe.

4. Adding Alpona Patterns

You can overlay SVG patterns.

Example:

Or use SVG mandala paths.

Best approach:

SVG background layer
Canvas animation layer
UI text layer
5. Making it React to Audio

If connected to a voice agent:

let amplitude = audioLevel * 20
wave = Math.sin(a*8 + t) * amplitude

Then the ring expands with speech.

Used by:

OpenAI realtime UI
ElevenLabs voice interfaces
Sarvam AI style references
6. Fonts That Feel “Indian but Modern”

Use fonts like:

Sora
Manrope
Hind
Noto Sans Devanagari

For bilingual UI.

7. If You Want the Real Premium Look

Best stack:

React
+ Framer Motion
+ WebGL (Three.js)
+ SVG patterns

Then you can create:

breathing mandala
ink diffusion waves
subtle alpona geometry
8. Advanced Idea (Super Authentic)

Instead of circular waveforms, use rangoli symmetry:

8-fold symmetry
12-fold symmetry

Wave expands like petals opening.

This feels very Indian but not cliché.

💡 My suggestion for your voice agent UI:

Combine:

dark indigo background
white alpona geometry
breathing mandala waveform
gold micro accents

That will look far more authentic than typical neon AI UIs.

Those animations you see in voice agents (like in ChatGPT real-time voice or ElevenLabs voice demos) are usually audio-reactive visualizations. They are not random animations — they respond to mic amplitude, frequency spectrum, or speaking state.

There are three main ways developers implement them, depending on the platform.

1. Web (Most common): Web Audio API + Canvas/WebGL

Most real-time voice apps on the web use:

Web Audio API → get mic audio levels
Canvas / WebGL / Three.js → render animation
Basic architecture
Microphone
   ↓
Web Audio API (AnalyserNode)
   ↓
Get amplitude / frequency data
   ↓
Feed into animation loop
   ↓
Canvas / WebGL animation
Example: Circular watercolor-like animation

This is a simplified structure.

const audioContext = new AudioContext();
const analyser = audioContext.createAnalyser();

navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
  const source = audioContext.createMediaStreamSource(stream);
  source.connect(analyser);
});

const dataArray = new Uint8Array(analyser.frequencyBinCount);

function draw() {
  requestAnimationFrame(draw);

  analyser.getByteFrequencyData(dataArray);

  const avg = dataArray.reduce((a,b)=>a+b)/dataArray.length;

  ctx.clearRect(0,0,width,height);

  const radius = 100 + avg * 0.3;

  ctx.beginPath();
  ctx.arc(width/2, height/2, radius, 0, Math.PI*2);
  ctx.fillStyle = `rgba(100,150,255,0.4)`;
  ctx.fill();
}

draw();

The radius expands with voice energy.

Then designers add:

blur
gradient
noise
multiple layers

to create the watercolor effect.

2. GPU Shader (How the really fancy ones work)

High-end voice UIs often use fragment shaders.

Example technologies:

Three.js
WebGL
ShaderToy style shaders

Instead of drawing circles, they generate procedural blobs.

Example concept:

noise field
+ radial distortion
+ audio amplitude
+ time

Pseudo shader logic:

color = noise(position + time)

radius = base + audio_amplitude * distortion

final_color = gradient * smooth_blob(radius)

This produces the liquid / watercolor effect.

3. Lottie animations (simple but not reactive)

Some apps fake the animation using:

Lottie
exported from Adobe After Effects

But this isn't actually audio reactive. It just loops.

4. The method used in most modern voice agents

Typical stack:

Mic audio
   ↓
VAD (voice activity detection)
   ↓
Amplitude / FFT
   ↓
Audio visualizer

Libraries commonly used:

Three.js
p5.js
Tone.js
D3.js
5. The ChatGPT / ElevenLabs style orb

Those usually combine:

1️⃣ Circular base
r = base_radius + amplitude
2️⃣ Perlin noise distortion
r += noise(angle + time) * distortion
3️⃣ Soft gradients
radial-gradient
4️⃣ Blur
canvas blur
or shader blur

Result:

liquid blob that pulses with speech
6. If you want the exact modern look

Most teams use:

React
 + Three.js
 + GLSL shaders
 + Web Audio API

Pipeline:

Mic → FFT → shader uniforms → GPU animation

This allows:

smooth 60fps
liquid motion
color morphing
low CPU usage
7. Example open-source visualizers

Good repos to study:

three-audio-visualizer
p5.sound examples
wavesurfer.js
8. If you're building a Pipecat voice agent

The recommended architecture is:

Frontend
   ↓
Mic capture
   ↓
Web Audio analyser
   ↓
Animation engine

Meanwhile:

Mic → WebRTC → Pipecat → STT → LLM → TTS

The visualization runs locally and doesn't depend on the STT/TTS.

✅ Important insight

The animation usually does NOT depend on the AI response.

It reacts to:

microphone amplitude
speaking state
VAD events

That's why it's extremely responsive.