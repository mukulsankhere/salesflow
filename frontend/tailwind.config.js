/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef4ff', 100: '#dce7fe', 200: '#c0d4fd', 300: '#94b7fb',
          400: '#6191f7', 500: '#3d6cf2', 600: '#274de7', 700: '#1f3ad4',
          800: '#2032ab', 900: '#1f3087', 950: '#172053',
        },
      },
      fontFamily: { sans: ['Inter', 'system-ui', 'Segoe UI', 'sans-serif'] },
    },
  },
  plugins: [],
}
