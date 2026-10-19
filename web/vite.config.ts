import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../damavand_bot/static',
    emptyOutDir: false,
    lib: { entry: 'src/main.tsx', formats: ['iife'], name: 'DamavandPanel', fileName: () => 'panel.js', cssFileName: 'panel' },
    cssCodeSplit: false,
    rollupOptions: { output: { assetFileNames: 'panel.[ext]' } },
  },
})
