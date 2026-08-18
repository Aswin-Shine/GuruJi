import { defineConfig } from "vite";

/**
 * No @preact/preset-vite on purpose.
 *
 * The preset exists mainly to add Babel + prefresh for component-state-preserving
 * HMR. That pulls a whole Babel dependency tree into the build, which is both the
 * largest install in the project and the largest supply-chain surface in it. Vite 8
 * bundles oxc, which compiles Preact JSX natively from the four lines below, and
 * Vite's built-in full-reload HMR is fine for an app this size.
 *
 * The result is a 17-package node_modules for the entire frontend.
 *
 * ponytail: full-reload HMR loses component state on save. Add @preact/preset-vite
 * only if that becomes an actual daily annoyance.
 */
export default defineConfig({
  // Vite 8 transforms with oxc (Rolldown), not esbuild. The `esbuild: { jsx }`
  // key from Vite <= 7 no longer exists; this is its replacement.
  oxc: {
    jsx: {
      runtime: "automatic",
      importSource: "preact",
    },
  },

  build: {
    // Matches the browserslist in package.json. Modern targets mean no legacy
    // transforms and no regenerator/core-js weight shipped to a budget Android phone.
    target: ["chrome111", "firefox128", "safari16.4"],
    cssTarget: ["chrome111", "firefox128", "safari16.4"],

    // CSP: 'unsafe-inline' must never appear in script-src. Vite's module-preload
    // polyfill is injected as an INLINE script, which would force it. Disabled —
    // every target browser above supports <link rel="modulepreload"> natively.
    modulePreload: { polyfill: false },

    // One CSS file, loaded once, cached forever. Code-splitting CSS for a
    // seven-screen app buys extra requests on a high-latency mobile network and
    // saves nothing.
    cssCodeSplit: false,

    sourcemap: false, // never ship sourcemaps to production
    reportCompressedSize: true,
    chunkSizeWarningLimit: 120, // kB — a tripwire, not a suggestion

    rollupOptions: {
      output: {
        // Content-hashed filenames are what make the immutable cache header in
        // nginx.conf safe. Do not remove the hash without removing that header.
        entryFileNames: "assets/[name].[hash].js",
        chunkFileNames: "assets/[name].[hash].js",
        assetFileNames: "assets/[name].[hash][extname]",
      },
    },
  },

  server: {
    host: "127.0.0.1", // never 0.0.0.0 — a dev server bound to every interface
    port: 5173, //        is a live read of your source on any hostile network
    proxy: {
      // Mirrors the nginx proxy in production, so dev and prod share one origin
      // model and there is no CORS-only-in-dev class of bug.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },

  preview: { host: "127.0.0.1", port: 4173 },
});
