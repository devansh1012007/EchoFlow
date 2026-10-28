<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/90b69359-ec5b-40bb-b341-135ce2d10918

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in [.env.local](.env.local) to your Gemini API key
3. Run the app:
   `npm run dev`

## More documentation

- [API_INTEGRATION.md](API_INTEGRATION.md): every backend route, the UI that calls it, and its test.
- Design system: `src/styles/tokens.css` is the single source of truth (a closed palette of deep black,
  dark gray, light gray, white, red and blue). `src/test/designSystem.test.ts` fails if an off-palette
  colour, gradient or undefined CSS variable appears in source.
- Public pages that render without signing in: `/terms`, `/support`, `/clip/:id?s=…`
  (resolved in `src/lib/publicRoutes.tsx`; `public/_redirects` serves `index.html` for every path).
- Terms and Conditions (`src/pages/Terms.tsx`) is a draft: items in `[square brackets]` are placeholders
  for business details the codebase does not establish and need legal review before publication.
