# How Dreachy works

A technical walkthrough of one exchange, from the moment you speak to the moment
the robot answers: which process handles each step, what crosses the network, and
what Dreachy itself contributes.

Dreachy is a thin layer. Nearly everything in the path below belongs to Pollen
Robotics' [`reachy_mini_conversation_app`][conv-app] or to the Reachy Mini SDK.
Dreachy supplies a persona, five tools and a settings page — roughly two hundred
lines of new code. Knowing which part is whose matters when something breaks.

[conv-app]: https://github.com/pollen-robotics/reachy_mini_conversation_app

## The three processes

Everything runs on the robot except the model.

| Where | What runs there | Owner |
| --- | --- | --- |
| Robot — `reachy-mini-daemon` | Motor control loop (~50 Hz), audio capture and playback, WebRTC media pipeline, the wobbler that drives head motion from audio, app lifecycle, the dashboard API | Reachy Mini SDK |
| Robot — the app process | The conversation loop, the realtime WebSocket connection, tool registry and dispatch, movement queue, settings page | Conversation app, with Dreachy's tools and profile loaded into it |
| Off-robot — Hugging Face | Speech recognition, the language model, turn detection, speech synthesis | Hugging Face realtime backend |

**There is no local language model.** This surprises people, so it's worth being
explicit: the robot does not run an LLM, and it does not transcribe or synthesise
speech locally. It streams microphone audio to a remote realtime model and plays
back the audio that comes home. The only machine learning that runs on the robot
is in the SDK — face detection for head tracking (unused by Dreachy, which runs
without a camera) and a small neural network used by the inverse kinematics.

What the robot does own is everything with a deadline: the motor control loop,
the audio pipeline, and the movements. Those never wait on the network.

## One exchange, step by step

Say you ask: *"What's new on the site?"*

### 1. Microphone to the app

The daemon captures audio and exposes it through the media pipeline. The app's
recording loop pulls frames and hands them to the realtime handler:

```
robot.media.get_audio_sample()   →   handler.receive((sample_rate, frame))
```

`console.py:861` — a plain loop reading frames at the pipeline's own sample rate
(16 kHz on this hardware). If the mic is muted,
or its volume is zero, this is where the exchange silently ends. (It happened to
us: a robot that appeared broken had its microphone volume set to 0.)

### 2. App to the model

A stereo frame is downmixed to its first channel, cast to int16, base64-encoded
and appended to the realtime session's input buffer over a WebSocket:

```
connection.input_audio_buffer.append(audio=…)
```

Audio leaves the robot here. The session was opened earlier with a configuration
that decides most of the conversation's behaviour (`huggingface_realtime.py:215`):

- **`instructions`** — Dreachy's persona, read from `instructions.txt`.
- **`tools`** — the JSON schema of every registered tool, Dreachy's five included.
- **`turn_detection: server_vad`** with `interrupt_response: true` — the *server*
  decides when you've stopped speaking, and a new utterance interrupts the robot
  mid-sentence.
- **`transcription`** — `gpt-4o-transcribe`, so the app receives a text transcript
  alongside the audio, which is what appears in the logs.
- **`voice`** — the speech synthesis voice, default `Aiden`.

### 3. Connecting, once per session

Before any of this, the app asks a Hugging Face Space proxy to allocate a session
and give it a WebSocket URL (`_build_realtime_client`). `HF_TOKEN`, or whatever
`hf auth login` stored on the robot, is the bearer token. Because allocation is
indirect, the backend can move without the app being re-released.

### 4. The model decides to call a tool

The model hears the question, matches it against the tool descriptions, and emits
a function call. The app sees `response.function_call_arguments.done` with a tool
name, a JSON argument string and a `call_id` (`huggingface_realtime.py:836`).

**This is the moment Dreachy's own code first runs.** Everything before it is the
conversation app and the backend. The model chose `drupal_whats_new` because of
two things Dreachy supplied: the tool's description, and the persona telling it
to answer only from site content.

### 5. The tool runs on the robot

The call goes to `BackgroundToolManager`, which runs it as its own asyncio task,
so a slow site cannot block the conversation. Dreachy's tool is a thin adapter:

```python
# dreachy/tools/drupal_whats_new.py
await asyncio.to_thread(drupal_whats_new, get_client(), limit=…)
```

The synchronous Drupal client runs in a worker thread, builds a JSON:API query and
makes the HTTP request:

```
GET https://your-site.example/en/jsonapi/node/article?sort=-created&page[limit]=5
```

Anonymous, read-only, no authentication. The language prefix comes from the
settings page. The client strips HTML, maps each content type's text fields, and
returns plain data — titles, types and human-readable ages, not raw JSON:API
documents. Keeping that shaping here rather than leaving it to the model is
deliberate: less to send, less to hallucinate around.

### 6. The result goes back to the model

The manager sends the result as a `function_call_output` item carrying the same
`call_id`, then requests a new response (`huggingface_realtime.py:619`). The model
reads the data and composes a spoken answer from it.

### 7. Audio comes home

Speech arrives as `response.output_audio.delta` events. The app's playback loop
converts each chunk to float32 mono and pushes it into the media pipeline:

```
robot.media.push_audio_sample(audio_frame)
```

`console.py:872`. The daemon plays it — and the same call feeds the wobbler, which
is why the head moves with the voice. That motion is computed on the robot from
the audio itself; the model never sends movement commands.

### The round trip

```
you speak
   │
   ├─ daemon: mic capture ──────────────────────── on robot
   ├─ app: record loop ───────────────────────────  on robot
   ├─ WebSocket ──────────────────────────────────▶ Hugging Face
   │     speech recognition, turn detection,
   │     the model, tool choice
   ◀──────────────────────────────────────────────  function call
   ├─ app: background tool manager ───────────────  on robot
   ├─ Dreachy: client → HTTP ─────────────────────▶ your Drupal site
   ◀──────────────────────────────────────────────  JSON:API response
   ├─ Dreachy: shape the result ──────────────────  on robot
   ├─ WebSocket ──────────────────────────────────▶ function_call_output
   │     the model composes the answer,
   │     speech synthesis
   ◀──────────────────────────────────────────────  audio chunks
   ├─ app: play loop ─────────────────────────────  on robot
   └─ daemon: playback + wobbler ─────────────────  on robot
you hear the answer, and the head moves with it
```

Three network hops: robot to Hugging Face, robot to your Drupal site, robot to
Hugging Face again. The Drupal request is usually the quickest of them — a few
hundred milliseconds against a warm site. A sleeping hosting sandbox is the
exception, and it's worth waking the site before a demo.

## The watcher is a different path

`drupal_watch_site` doesn't fit the shape above, and the reason is worth knowing
if you write a tool of your own.

The background tool model allows one spoken announcement per call, when the
tool's coroutine returns. That suits `what's new`. It does not suit something
that runs for hours and must react repeatedly.

So the watcher starts its own persistent asyncio task and never returns until
stopped. Each poll compares the newest `created` timestamp against the last one
it saw. When something is new, it acts on the hardware directly:

```python
deps.reachy_mini.media.play_sound("wake_up.wav")
await play_reaction(REACTIONS["perk_up"], deps)     # antennas flare, head lifts
```

`play_reaction` queues moves on the `MovementManager` and waits out each step's
real duration. **Neither call goes near the model.** No tokens, no network, no
speech — which is exactly why the reaction feels immediate rather than narrated,
and why it still works while the model is busy saying something else.

Two consequences of holding state in a long-lived task: the watcher re-reads the
shared client on every poll, so a site URL saved on the settings page reaches it
without a restart; and after a failed poll it backs off exponentially, checking
between sleeps whether the URL changed, so a newly configured site isn't stuck
behind a five-minute wait.

## Who owns what

| Concern | Dreachy | Conversation app | SDK / daemon | Hugging Face |
| --- | --- | --- | --- | --- |
| Persona and greeting | ✅ `profile/dreachy/` | loads it | | applies it as session instructions |
| Tool definitions | ✅ five Drupal tools | registry, schema, dispatch | | chooses which to call |
| Talking to Drupal | ✅ `client.py` | | | |
| Speech recognition | | | | ✅ |
| Language model | | | | ✅ |
| Speech synthesis | | | | ✅ |
| Turn detection | | | | ✅ server VAD |
| Audio capture and playback | | loops | ✅ pipeline | |
| Head motion while speaking | | | ✅ wobbler | |
| Physical reactions | ✅ `perk_up` | movement manager | ✅ motor control | |
| Settings page | ✅ site URL, language, extra instructions | mounts the app | serves `custom_app_url` | |
| Session allocation, auth | | ✅ | | ✅ |

## How Dreachy plugs in

`dreachy/main.py` is a `ReachyMiniApp` subclass that does three things before
handing over:

1. **Renders the profile** into the writable instance path, appending any extra
   instructions from the settings page to the built-in persona rather than
   replacing it, so the guardrails survive whatever an installer types.
2. **Sets three environment variables** the conversation app's `Config` reads at
   import time — the custom profile name, the profiles directory and the external
   tools directory. This is why the import of `run()` happens *inside* the method
   and not at module level.
3. **Registers `GET`/`POST /api/config`** on the settings app that the dashboard
   serves, then calls `reachy_mini_conversation_app.main.run()` and does not
   return until the app stops.

Tools are discovered by filename from the external tools directory, which is why
shared helpers live in `tools/_shared.py`: the loader skips files starting with an
underscore.

There is no forked behaviour anywhere. Dreachy configures the conversation app and
delegates to it.

## Failure modes, and where to look

| Symptom | Likely cause | Where |
| --- | --- | --- |
| Robot says nothing at all | Microphone volume at 0, or no realtime session | `/api/volume/microphone/current` on the daemon; app logs for session allocation |
| Answers, but claims to know nothing | Drupal request failing — unreachable site, wrong language prefix, JSON:API off | Daemon logs show the httpx request and status |
| Every request 404s | Language prefix set when the site is single-language, or missing when it's multilingual | Settings page |
| Says it will read an article, then stops | The model acknowledged without emitting the tool call. Known, intermittent | Ask again |
| Reaction never fires | Watcher polling the wrong site, or nothing published since the baseline | Logs: `drupal_watch_site: new content detected` |
| Site pulse counts look wrong | Counts are capped at `pulse_sample_limit` (50) per type — core JSON:API has no collection count | `config.py` |

The daemon streams its journal over a WebSocket at `/logs/ws/daemon`, which
carries the app's own output too. It's the single most useful debugging surface on
the robot, and it's how every bug listed above was actually found.

## What this means if you fork it

The seam worth understanding: **the model decides, Dreachy fetches, the robot
moves.** A new tool is a file in `dreachy/tools/` that returns plain data, plus a
line in `tools.txt`, plus a sentence of description good enough for the model to
choose it. You don't touch audio, speech or movement.

The things Dreachy currently hardcodes — which content types exist, which fields
hold readable text, which language prefix — are the obvious next thing to move
into the Drupal site itself, so a site builder configures the robot from Drupal's
admin UI instead of editing Python.
