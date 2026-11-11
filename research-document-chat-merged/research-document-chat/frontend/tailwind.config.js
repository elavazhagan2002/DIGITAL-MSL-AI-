import typography from '@tailwindcss/typography';

/**
 * Sciagen theme. The `cyan` and `blue` scales are re-pointed at the brand colours
 * (cyan #02B4D8, blue #0078B5, navy #030460), so there is no teal or green anywhere in the UI.
 */
const sciagenCyan = {
  50: '#ecfbff',
  100: '#d0f4fc',
  200: '#a4e8f8',
  300: '#6dd7f0',
  400: '#38cbea',
  500: '#00b4d8',
  600: '#0096c7',
  700: '#0a79a8',
  800: '#0e5f86',
  900: '#0e4a6a',
  950: '#082f49',
};

const sciagenBlue = {
  50: '#e6f5fb',
  100: '#cdebf7',
  200: '#9bd7ef',
  300: '#5fbde3',
  400: '#2a9fd2',
  500: '#0b8cc6',
  600: '#0078b5',
  700: '#00649a',
  800: '#0a517d',
  900: '#0c4263',
  950: '#072a42',
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    screens: { sm: '640px', md: '768px', shell: '960px', lg: '1024px', xl: '1280px', '2xl': '1536px' },
    extend: {
      colors: {
        // Token classes: bg-card, text-fg, border-line, bg-accent, text-accentfg, ...
        page: 'var(--page)',
        card: 'var(--card)',
        fg: 'var(--fg)',
        muted: 'var(--muted)',
        line: 'var(--border)',
        accent: 'var(--accent)',
        accentfg: 'var(--accentfg)',
        link: 'var(--link)',
        selbg: 'var(--selbg)',
        selb: 'var(--selb)',
        soft: 'var(--soft)',
        field: 'var(--field)',
        fieldb: 'var(--fieldb)',
        'err-bg': 'var(--err-bg)',
        'err-fg': 'var(--err-fg)',
        'err-line': 'var(--err-border)',
        'ok-bg': 'var(--ok-bg)',
        'ok-fg': 'var(--ok-fg)',
        'warn-bg': 'var(--warn-bg)',
        'warn-fg': 'var(--warn-fg)',
        'warn-line': 'var(--warn-border)',
        brandnavy: 'var(--brand-navy)',
        brandcyan: 'var(--brand-cyan)',
        brandblue: 'var(--brand-blue)',
        navy: 'var(--navy)',
        navyline: 'var(--navy-line)',
        onnavy: 'var(--on-navy)',
        onnavytext: 'var(--on-navy-text)',
        onnavymuted: 'var(--on-navy-muted)',
        navyhover: 'var(--nav-hover)',
        navyactive: 'var(--nav-active)',
        navyoutline: 'var(--nav-outline)',
        scrim: 'var(--scrim)',
        cyan: sciagenCyan,
        blue: sciagenBlue,
        sciagen: {
          navy: '#030460',
          navy950: '#050B3D',
          navy900: '#0A1158',
          navy800: '#0E1A7A',
          ink: '#0B0F5C',
          cyan: sciagenCyan,
        },
      },
      boxShadow: {
        card: 'var(--shadow-card)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [typography],
};
