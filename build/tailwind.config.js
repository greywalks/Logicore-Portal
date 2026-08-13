/**
 * Production Tailwind build config for the Logicore Invoice Suite.
 *
 * Why this exists: the app previously loaded Tailwind at runtime via
 * <script src="https://cdn.tailwindcss.com">, which Tailwind itself
 * documents as NOT for production use. It means the ENTIRE UI's styling
 * depends on that CDN being reachable every time the app loads — any
 * corporate firewall, ad-blocker, offline demo, or CDN outage silently
 * breaks the whole interface (confirmed by deliberately blocking the CDN:
 * pages rendered with zero layout/spacing/color, looking "empty").
 *
 * This config compiles a static stylesheet (static/css/tailwind.css)
 * instead, so styling has no runtime network dependency at all.
 *
 * Rebuild whenever templates/index.html or static/js/app.js gain new
 * Tailwind classes:
 *   cd build && npm install && npm run build
 */
module.exports = {
  content: [
    "../templates/index.html",
    "../static/js/app.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'IBM Plex Sans', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
        display: ['Space Grotesk', 'sans-serif'],
      },
      colors: {
        ink:    { DEFAULT: '#0a0d12', 50: '#12161d', 100: '#1a2029', 200: '#232a36' },
        steel:  { DEFAULT: '#334155', light: '#475569' },
        accent: { DEFAULT: '#2fd8a6', dim: '#17a87e', glow: '#7cf0c7' },
        ok:     { DEFAULT: '#22c55e' },
        warn:   { DEFAULT: '#ef4444' },
      },
    },
  },
}
