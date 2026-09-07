import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    outDir: '../../dist/browser',
    rollupOptions: {
      input: {
        popup: './src/popup.html',
        option: './src/option.html',
        background: './src/background.ts',
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === 'background' ? 'background.js' : 'assets/[name]-[hash].js',
      },
    },
  },
  resolve: { alias: { '@': '/src' } },
  publicDir: 'res',
})