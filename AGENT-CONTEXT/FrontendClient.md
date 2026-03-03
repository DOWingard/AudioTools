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
