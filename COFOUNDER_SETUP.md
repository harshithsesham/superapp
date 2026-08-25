# Co-founder setup (shared deployment)

You need: a Mac with Xcode + Node, and two values Harshith gives you
out-of-band (never over the repo): the server URL and YOUR bearer token.

## Phone app

```bash
git clone <repo-url> && cd super-app/apps/mobile
npm install
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer   # once
export SUPERAPP_API_URL=https://app.yourdomain.com
export SUPERAPP_API_TOKEN=<your token>
npx expo run:ios          # simulator; add --device for your iPhone
```

## Connect your Gmail (read-only at first)

```bash
curl -s https://app.yourdomain.com/v1/gmail/auth-url -H "Authorization: Bearer $SUPERAPP_API_TOKEN"
```
Open the returned URL, approve ("Google hasn't verified this app" → Continue —
it's our own app in Testing mode; expect to re-approve weekly for now).

## What to test

- My Hub: does the glance tell you the truth?
- Inbox: tap Sync after new mail lands in your Primary tab. Judge every tier
  call — misfiled mail is the #1 feedback we need.
- Drafts: edit before sending (edits teach it your voice). Send is always your tap.
- Nutrition/Stylist: photograph a meal and a couple of garments.

Everything you see is yours alone — separate memory, separate mail, separate
closet. Note: the server admin (Harshith) can technically read the shared
database; you've both acknowledged this.
