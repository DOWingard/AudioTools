# Context: FrontendClient — React, UI, Auth State

> **Scope:** Bugs and failure patterns in React components, client-side auth state management, hooks, and quota/gate logic in `ui/src/`.

---

## Failure Patterns & Remediation

### 1. Free-tier quota consumed before API call succeeds *(Found: 2026-03-02)*

*   **Symptom:** Free user's `daily_free_downloads` counter decrements even when the processing API returns a 5xx error or the request fails entirely. User loses one of their 3 daily uses without receiving a result.
*   **Root Cause:** `useGatedRun()` in `ui/src/AuthContext.jsx` called `POST /auth/usage/consume` **before** invoking the wrapped processing function. The processing fetch hadn't happened yet at consume time, so any subsequent API failure was undetectable and the quota was already gone.
*   **Remediation Plan:**
    1.  Remove `POST /auth/usage/consume` from `useGatedRun()`. Replace with a client-side guard: `if (profile?.daily_free_downloads <= 0) { openLimitModal(); return; }` — this uses the cached profile state (updated on every successful consume) for a fast pre-check.
    2.  Add a `useConsumeUsage()` hook to `AuthContext.jsx`. It calls `POST /auth/usage/consume` and updates `profile.daily_free_downloads` from the `remaining` field in the response. Wrap in `useCallback([getToken, profile, setProfile])`.
    3.  In each processing tab's `run()` function, call `await consumeUsage()` immediately **after** the `if (!resp.ok) throw` guard — i.e., only on a confirmed 2xx API response.
    4.  Affected tabs (all 7 use `useGatedRun`): `FormatConverterTab`, `AudioCutterTab`, `AudioJoinerTab`, `KaraokeTab`, `StemSeparatorTab`, `SyncTagTab`, `AudioAnalyzerTab`.
    5.  `StorageConfirmModal`'s `onYes` calls `run()` directly (bypasses `gatedRun`). With this fix that is now **correct** — quota only flows through `run()` on API success, not through the gate.

### 2. Missing React Hooks imports causing ReferenceError *(Found: 2026-03-02)*

*   **Symptom:** UI displays a blank white screen when navigating to `StemSeparatorTab`, `SyncTagTab`, or `KaraokeTab`.
*   **Root Cause:** The components used the `useEffect` hook but it was not imported from `'react'`. This throws a `ReferenceError: useEffect is not defined` during the initial component render cycle, crashing the React tree.
*   **Remediation Plan:**
    1.  Add `useEffect` to the destructuring import from `'react'` at the top of the affected files: `import { useState, useRef, useEffect } from 'react';`

### 3. Vite chunk size limit warning *(Found: 2026-03-02)*

*   **Symptom:** `npm run build` outputs a warning: `(!) Some chunks are larger than 500 kB after minification.`
*   **Root Cause:** All third-party dependencies (`react`, `@clerk/clerk-react`, `wavesurfer.js`, `jszip`) were being bundled into a single oversized `index-[hash].js` chunk.
*   **Remediation Plan:**
    1.  Update `ui/vite.config.js` to include `build.rollupOptions.output.manualChunks`.
    2.  Explicitly map large dependencies to dedicated vendor chunks (e.g. `vendor_react`, `vendor_clerk`, `vendor_wavesurfer`, `vendor_jszip`) to split the payload and resolve the warning.
