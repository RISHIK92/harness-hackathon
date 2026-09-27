# Photon Whisper for Google Meet

A Chrome extension that whispers inside Google Meet with **no bot in the call**.
It reads Meet's own live captions, sends settled lines to Photon's whisper
endpoint, and shows cited answer suggestions in Chrome's side panel — only to you.

## Install (unpacked)

1. `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick this `extension/` folder.
2. In Photon: **My agent → Meet extension → Make a pairing code**.
3. Click the extension's toolbar icon to open the side panel, paste the Photon
   address and the code, and **Connect**. Chrome asks once for access to that address.

## Use

Join a Meet call and turn captions on (**CC**, or press `c`). The panel shows
`listening · N lines heard`; when someone asks a question, a suggestion appears
with a ✅ / ⚠️ label for whether it is safe to say as-is. You can also ask Photon
privately from the box at the bottom.

## What it can reach

The pairing code is swapped for a token that works **only** on Photon's whisper
routes, **only** in the workspace you paired from, and is revocable per browser
(My agent → Meet extension → Disconnect). It never sees your password or your login token,
and the Meet page itself never sees the token — every API call is made from the
extension's service worker.

## Limits

- It hears what Meet captions: captions must be on, and caption quality is Meet's.
- Meet's caption markup changes without notice. `captions.js` tries three
  strategies (jsname attributes, class names, the labelled captions region); if a
  Meet update breaks all three, the panel says captions are off while they are on —
  update the selectors there.
- Chrome/Edge only. For Zoom, Teams, or meetings you are not in, use the notetaker
  bot on Photon's Whisper page.

## Tests

`node --test test/*.test.js` — the caption tracker (growing and rewritten
captions become one line each) and the three DOM strategies against Meet-shaped markup.
