import neumorphism from 'tailwindcss-neumorphism-ui';

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:        'var(--bg)',
        surface:   'var(--surface)',
        surface2:  'var(--surface2)',
        border:    'var(--border)',
        text:      'var(--text)',
        muted:     'var(--muted)',
        accent:    'var(--accent)',
        nmshadow:  'rgb(var(--nm-shadow-rgb) / <alpha-value>)',
        nmhighlight: 'rgb(var(--nm-highlight-rgb) / <alpha-value>)',
        green:     '#3fb950',
        yellow:    '#d29922',
        red:       '#f85149',
        blue:      '#388bfd',
        purple:    '#bc8cff',
      },
    },
  },
  plugins: [neumorphism],
}
