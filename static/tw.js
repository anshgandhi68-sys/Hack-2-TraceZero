// Shared Tailwind CDN config (load right after cdn.tailwindcss.com).
// Design language: dark editorial + pale information sections, mono UI type,
// dot-matrix display numerals, single orange action accent.
tailwind.config = {
  theme: {
    extend: {
      fontFamily: {
        sans: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        jetbrains: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        bubbledot: ['Doto', '"JetBrains Mono"', 'monospace'],
        dot: ['Doto', '"JetBrains Mono"', 'monospace'],
      },
      colors: {
        accent: '#F64302',
        pale: '#f0eded',
        bento: '#E8E5E9',
        card: '#EBE8EB',
        ink: { 950: '#07080c', 900: '#0b0d13', 850: '#10131b', 800: '#151923', 700: '#1e2330', 600: '#2a3142' },
      },
      transitionTimingFunction: { out: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    },
  },
};
