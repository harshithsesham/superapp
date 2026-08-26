# Co-founder / tester setup

## The easy way (TestFlight — once live)

1. Accept the TestFlight invite from your email, install **Super App**
2. Open it → **Continue with Google** → pick your account
3. On My Hub, tap **Connect inbox** → approve (the "Google hasn't verified
   this app" warning is expected — Continue; re-approve weekly while we're in
   Google's Testing mode)

That's everything. Your mail, meals, closet, and money are yours alone —
isolated per account. Nothing sends from your inbox without your explicit tap
on a draft. Note: the server admin can technically read the shared database;
we've acknowledged this between us.

Prereq on our side: your Gmail must be on the Google console test-user list.

## The dev way (before TestFlight, or to hack on it)

Requires a Mac with Docker, Xcode, and Node.

```bash
git clone <repo-url> && cd super-app
claude        # then type: /setup   — Claude walks you through everything
```

Point the app at the shared server when it asks (or use the in-app
"Choose server" on the sign-in screen), then Continue with Google as above.

## What to test hardest

- Inbox tiers: is anything misfiled? That's the #1 feedback we need.
- Drafts: edit before sending — edits teach it your voice. Check the signature.
- The Hub glance: does it tell you the truth at 7am?
