import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
    plugins: [react()],
    server: {
        port: 5600,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
            '/auth': {
                target: 'http://localhost:8001',
                changeOrigin: true,
            },
        },
    },
    build: {
        rollupOptions: {
            output: {
                manualChunks: {
                    vendor_react: ['react', 'react-dom'],
                    vendor_clerk: ['@clerk/clerk-react'],
                    vendor_wavesurfer: ['wavesurfer.js'],
                    vendor_jszip: ['jszip'],
                },
            },
        },
        chunkSizeWarningLimit: 600,
    },
});
