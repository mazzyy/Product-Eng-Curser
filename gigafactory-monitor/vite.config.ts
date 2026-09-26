import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Expose on the local network so the prototype can be opened on a tablet.
    host: true,
    port: 5173,
  },
});
