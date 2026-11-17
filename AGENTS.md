# AGENTS.md

Instructions for coding agents working in this repository.

## What this is

`bot.py` — **Debugger Chan**, the Discord bot for the Freebuff community server. One file,
~2,650 lines, `discord.py` 2.x, no framework, no database. It is a command bot plus a moderation
bot plus a music player.

## Layout

| Path | Purpose |
| --- | --- |
| `bot.py` | the entire bot — config constants, helpers, `on_message`, commands, music, filter |
| `webchat.html` | the page served at `http://localhost:6767`, re-read on every load (no restart to edit it) |
| `banned_words.txt` | extra banned words/phrases, one per line, `#` comments, spaces means phrase |
| `unbanned_words.txt` | terms removed with `!removeword` that were built into the code (created at runtime) |
| `blacklist.txt` | user IDs whose messages are always removed |
| `whitelist.txt` | user IDs whose messages the filters never remove, set with `!whitelist` |
| `punished.txt` | user IDs under `!punish` and the unix time each one expires |
| `user_memory.json` | remembered people and facts, written by the bot at runtime |
| `flag_case.json` | state for `!action` |
| `debugger-chan.service` | systemd unit used on the server |
| `action_harness.py` | dev tool: drives the `!action` agent against the server and checks its claims |
| `.env` | `DISCORD_TOKEN`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `FLAG_API_KEY`, `APINEX_PER_MINUTE`, `SUDO_PASSWORD` — never read, print or commit this file |

Runtime state files are gitignored. Editing them by hand while the bot runs will be overwritten.

## Running

```bash
.venv/Scripts/python.exe bot.py     # Windows, from the repo root
```

Import it instead of running it when testing; `client.run()` is behind `__main__`. Test harnesses
should replace `bot.client` with a fake object exposing `.user` and call the real `bot.on_message`
with fake message objects — that is how every change here was verified.

## Where it runs

The live bot now runs on the owner's Windows laptop, straight from this repo, so the working
copy **is** what serves Discord — there is no deploy step and no second machine to copy to.

```bash
.venv/Scripts/python.exe bot.py     # the project venv, what the docs assume
python bot.py                       # also works if that interpreter has the requirements
```

Whichever interpreter is used needs the requirements installed **in that interpreter**. This
is the trap voice chat falls into: `discord-ext-voice-recv` can be present in `.venv` while the
bot was started with a different `python`, and the only symptom is `!talk` saying the package
isn't installed. The startup line and `!talk` now print the import error and `sys.executable`
for exactly this reason — read that before installing anything again.

`!restart` from Discord reloads in place (`os.execv`).

Never run two copies on one token: double replies, double deletions, two threads per webhook.

## Behaviour that is deliberate

- **No text chat replies.** The AI chat reply (answering a mention, the phrase `debugger chan`,
  a reply to her, or a DM) was removed on purpose: it mostly answered "I'm a little overloaded".
  Messages that are not commands are only filtered and remembered, never answered. Do not add
  it back, and do not add chime-in, idle chatter or random reactions. She speaks only in reply to
  commands and in an `!talk` call, which she was explicitly invited into (see below).
- **Unauthorized members get silence, not refusals.** `may_use_commands()` gates every command.
  A stranger who runs one gets nothing at all.
- **Moderation deletes and logs.** Every removal writes one embed to `FILTER_LOG_CHANNEL_ID`
  (`1529951666714054746`) naming the rule, screener, term and full message.
- **Filter exemptions.** `FILTER_FREE_CHANNEL_IDS` bypasses every filter, threads included.
  `!filteroff` disables the filter globally until `!filteron` or a restart.
- **`!punish` impersonates, it does not time out.** `!punish <id or ping> [1m|1h|1d]` adds a
  member to `PUNISHED` (written to `punished.txt` as `id expiry`); their next messages are deleted
  and posted again through a cached webhook wearing their display name and avatar, saying one of
  `PUNISH_LINES` (`uwu`, `nyaa~`, `baka!` …). The duration is optional and an hour by default; a
  record without a time in the file (the old format) is given an hour on load. Expiry is lazy —
  checked when the member next speaks and at the start of every `!punish`/`!unpunish`, there is no
  timer — and nothing is announced when it runs out. `!unpunish <id or ping>` stops it early, and
  also reports "nobody there was being punished" if the punishment has already expired. Both share
  the command role gate, so only authorized members reach them; strangers get silence. Punishment
  runs before the word filter and ignores the whitelist, since it is an explicit moderator action.
  Needs Manage Messages plus Manage Webhooks; a message that cannot be deleted is left alone rather
  than replaced.
- **Whitelist beats the filter.** `!whitelist <id or ping>` (role gated, listed in
  `KNOWN_COMMANDS`) adds a member to `WHITELIST`, written to `whitelist.txt`; `filter_message` returns
  early for them, so nothing the filter does removes their messages. `!whitelist` with no argument
  lists who is on it.
- **AI screen is advisory and guarded.** A verdict is only acted on if it quotes words that are
  actually in the message; generic terms (`user id`, `error`, …) are rejected. Keep both guards.
- **No email auto-removal.** There is no email scanning, deletion or notice. Do not add one back.
- **Sticky support notice.** `!sticky <text>` sets one message; it is posted as an embed (in the
  title, which is the biggest text Discord offers) into every new thread in
  `TECH_SUPPORT_CHANNEL_ID` five seconds after the thread is created. `!removesticky` stops it
  and new threads get nothing. The state is in memory only, so a restart clears it.
- **General sticky.** `!stickygeneral <text>` posts the same embed in `GENERAL_CHANNEL_ID` every
  30 minutes until `!removestickygeneral`; setting it again replaces the text and restarts the
  timer, never running two timers. Also in memory only.

## `!talk`, voice chat

`!talk` joins the caller's voice channel and holds a spoken conversation until `!talk stop`.
Three providers, each replaceable on its own:

- **Hearing:** Whisper `whisper-large-v3` on Groq (`/openai/v1/audio/transcriptions`).
- **Thinking:** `nvidia/nemotron-3.5-lightning:free` on OpenRouter. It is a reasoner, so the call
  sends `reasoning: {"enabled": false}` and retries with a bigger budget if the content is empty;
  without that switch it answers inside its thinking block and comes back as silence.
- **Speaking:** `fish-audio/s2.1-pro-free:free` on OpenRouter's `/api/v1/audio/speech` endpoint
  (not `/chat/completions`, which rejects TTS models), voice `TTS_VOICE_ID`, `response_format`
  `mp3` only (`pcm` is the other option, there is no wav).

Receiving needs `discord-ext-voice-recv` (in `requirements.txt`); without it the command says so
and everything else still runs. The bot connects `self_mute=False, self_deaf=False`: deafened
would mean hearing nothing at all, and a muted bot shows a crossed-out mic that looks broken,
even though she never transmits anyway (only playback is used). The state is sent again once the
connection is up (`VoiceSession.microphone_on`), because the join update alone can leave her
showing the previous music connection's deafened mic; running `!talk` while already in the
channel re-sends it, so a muted session can be fixed without a restart.

**DAVE (E2EE) channels.** discord.py 2.7 negotiates DAVE and encrypts outgoing audio, but has no
receive path; `voice_recv` predates DAVE and hands the still-encrypted payload to libopus, which
raises `OpusError: corrupted stream` and kills its router thread, ending the call. `patch_voice_recv_dave()`
wraps `voice_recv.opus.PacketDecoder._decode_packet` to run `davey`'s `DaveSession.decrypt` first
(user id resolved from the packet ssrc), and now turns a packet that still will not decode into
silence instead of a dead thread. Removing that patch re-breaks every DAVE channel.

A packet that raises `NoValidCryptorFound` means Discord has not handed her that speaker's key
yet - normal for a moment after joining. It is dropped as silence, never passed to libopus.
A packet with no DAVE protocol marker (`UnencryptedWhenPassthroughDisabled`) is not DAVE at all -
plain Opus or an SFU silence packet - and is passed through untouched, as the protocol says;
dropping those made her deaf to whoever they came from.

The transcript lines are labelled `Name: what they said` so she can tell who is speaking; small
models echo that shape back, so `strip_speaker_prefix()` removes a leading label that really is a
member's name before the reply is spoken.

- **A dropped call is not a dead session.** The monitor checks the connection and reconnects (up
to a few attempts, ignoring the moment a reconnect spends disconnected); if it cannot, the session  closes, removes itself from `VOICE_SESSIONS` (otherwise `!talk` claims it is already listening when
  she is not in the call at all) and says so in the text channel. The same monitor calls `listen()`
  again if the connection is up but the reader has stopped, so a dead reader no longer means silence.
- **Log levels.** `voice_recv.reader`, `voice_recv.gateway` and `discord.player` are pinned to
WARNING: at INFO they log an RTCP report a second and a line per websocket frame.

- **Speech detection is local and cheap:** per-speaking-user RMS against `VOICE_SPEECH_RMS` on
  the receive thread, 20 ms frames, 0.3 s pre-roll, 0.8 s of quiet closes an utterance, under
  0.3 s of speech is ignored as a cough. Nothing is uploaded until an utterance ends.
- **She is interruptible.** ~100 ms of speech from anyone while she is playing stops playback
  and cancels the in-flight reply, and the person who talked over her is always answered.
  Use `voice.stop_playing()`, never `voice.stop()`: on a `VoiceRecvClient` the latter also ends
  listening, so one barge left her deaf and silent for the rest of the call.
- **Turn-taking, not eavesdropping.** She answers when her name is in the sentence (the check
  also squashes the transcript, so Whisper's one-word "Deepoggerchan" still counts), when the
  sentence is a question, when it is a short greeting aimed at the room ("hey", "hello all"),
  when she is mid-conversation (spoke within `VOICE_REPLY_WINDOW`, same speaker), or when she was
  just interrupted. Other chat in the call is transcribed into her history and left alone — the
  first thing anyone says to her must not be met with silence, or the whole feature reads broken.
- **One connection per guild.** `!talk` stops music, `!p` ends a talk session, and `!leave`
  ends either. `VOICE_SESSIONS` also lives in memory only, so a restart drops the call.

## `!radio`, Freebuff Radio

`!radio on` joins the caller's voice channel and loops `RADIO_PLAYLIST_URL` forever; `!radio off`
takes it off air. The last ten seconds of every song are the link: the bed fades down to
`RADIO_DUCK_VOLUME` and she talks over it from the booth, then the next song starts aloud again.

- **One source, two streams.** discord.py plays one source at a time, so `RadioSource` reads 20 ms
  frames from the duckable bed and, once a link is attached, from the spoken clip as well, and
  `radio_mix` adds them. Ducking is `PCMVolumeTransformer.volume`; a link never calls `voice.stop()`.
- **Timing.** `_link` starts with the song: it sleeps until `duration - RADIO_SPEECH_LEAD - RADIO_DUCK_SECONDS`,
  writes and voices the line (`radio_script` -> `ask_voice`, then `synthesise_speech`), sleeps until
  `duration - RADIO_DUCK_SECONDS`, fades the bed, attaches the clip. `_preload` resolves the next
  song in the same breath, so the transition never waits on yt-dlp.
- **Opening.** `_open_show` speaks one line with the music stopped before the first track, so the
  station is never a silent join.
- **The station outranks the rest.** `!radio` stops `!talk` and the music player; `!talk`, `!p`
  and `!leave` each stop the station. `RADIO_STATIONS` is memory only, so a restart is off air.
  A lost voice connection closes the session and says so in the text channel.
- **Fallbacks.** If the model or the speech endpoint is down the link becomes a
  `RADIO_FALLBACK_LINES` line and the songs keep playing; a playlist entry that will not resolve is
  skipped rather than fatal.
- **Requests.** `!req <song name or link>` (role gated like `!radio`, only while on air) resolves
  the track with `search_track` and appends it to `RadioStation.requests`; the broadcast loop plays
  that queue ahead of the playlist, so a request lands right after the current song and the
  playlist resumes after it. Whoever requested it (`Track.requester`) is named in her link
  (`radio_script`'s `requester` argument; the fallback says "a request from {name}. here comes
  {title}.") and in the now-playing announcement. In memory only, so a restart drops the queue.

## Web chat, `http://localhost:6767`

`start_webchat()` runs from `on_ready` (once, guarded against reconnects) and serves a
Discord-like page from `webchat.html` on the bot's own event loop (`aiohttp.web`, already a
discord.py dependency). Whoever uses it speaks **as the bot**: pick a server/channel/thread or a
DM, read history (50 at a time), send, reply (with or without a ping), DM anyone by user id.

- **Loopback only.** Bound to `127.0.0.1`, and `webchat_guard` refuses any request whose `Host`
  or `Origin` is not localhost - that stops a web page in the owner's browser posting here via
  DNS rebinding. Do not bind `0.0.0.0` or drop the guard; there is no login.
- **Live.** `on_message` (first thing, before every gate), `on_message_edit` and
  `on_raw_message_delete` push to every open tab over `/ws`. The push is wrapped so a web error
  can never cost a moderation action or a reply.
- Messages sent from the page go out as plain `channel.send`, so they skip her filters and her
  chat logic, exactly like the owner typing as the bot.
- If 6767 is taken (often the old process during `!restart`) it retries for ~20 s, then prints
  that the web chat is unavailable; the bot itself carries on.

## `!action`, the server agent

The action agent runs as `roland` on the Mint server, so its shell *is* the server: full
filesystem, systemd, apt, network. `!action` plans one command at a time (`ACTION_SYSTEM`, json
only) and feeds the real exit code and output back.

- Model: `recursive/flint-v1.0:free` on OpenRouter (`ask_openrouter_json`). The moderation screen
  keeps its own model on APINEX; don't merge them.
- **Planning must never be brittle.** This model sometimes replies with its reasoning instead of
  json. `parse_action_json` accepts fences, prose around the object, single quotes and several
  objects; `ask_action_plan` retries three times with `ACTION_JSON_NUDGE`; an empty reply counts as
  an unusable plan rather than an error. All of it feeds `ACTION_LAST_REPLY`, which the failure
  message shows — never replace that with "try rephrasing". For the same reason `SUDO_WORTHY`
  auto-elevates `systemctl start|stop|restart|enable|disable`, `service` and `apt` for the owner,
  because one forgotten `sudo` used to cost a whole step.
- `sudo` works because `shell_command()` pipes `SUDO_PASSWORD` into `sudo -S`, wrapping the rest in
  `bash -c` so redirects and pipes stay privileged.
- **Only the owner may sudo** (`sudo_allowed`) and only the owner's `dangerous` steps skip the
  `!action confirm` prompt. Other role holders still confirm.
- **Anti-falsehood guards, all load-bearing:** a `done` with no command in the transcript is sent
  back once; a final answer after a non-zero exit gets "the last command exited N" appended;
  the prompt forbids answering without a proving command. Do not loosen these — the model is
  small and happily reports states it never read.
- `action_harness.py` is how this is tested: real prompt, real model, commands over SSH, plus an
  independent probe the model never sees, compared against its answer. `--suite` is the smoke
  test (5 cases, one of them sudo); `--local` runs the commands on this machine instead.

## Commands

`!commands` `!addword` `!removeword` `!whitelist` `!punish` `!unpunish` `!filteron` `!filteroff` `!sticky` `!removesticky` `!stickygeneral`
`!removestickygeneral` `!action` `!restart` `!info` `!test` `!talk` (role gated),
`!a` `!off` `!offai` `!onai` (owner only), `!p` `!skip`/`!s` `!leave`/`!l` `!radio` `!req` (music/radio, role gated).

A new command must be added to `KNOWN_COMMANDS`, dispatched in `on_message`, listed in
`COMMANDS_HELP` (what `!commands` shows), and added to `OWNER_ONLY_COMMANDS` if it is owner-only. Otherwise it falls through to chat and reaches
unauthorized users.

## Conventions

- Discord IDs are module-level constants with a comment saying what the channel/role is.
- `print()` is the logging mechanism; messages are lowercase, plain, and describe what to do next.
- Errors from Discord are caught per call (`discord.DiscordException`) and reported, never fatal.
- Async everywhere; no blocking calls in handlers (`yt-dlp` work is already thread-offloaded).
- British phrasing in chat strings ("filters", "screened"). Keep the voice casual.

## Verify before claiming done

1. `python -c "import py_compile; py_compile.compile('bot.py', doraise=True)"`.
2. Drive `on_message` with fake objects for the changed path (the actual case, plus one that must
   still be filtered). Use temporary copies of the word lists, never the real ones.
3. Confirmed output, then deploy — a change that only exists on this machine is not deployed.
4. Check `journalctl -u debugger-chan` for tracebacks after the restart.
