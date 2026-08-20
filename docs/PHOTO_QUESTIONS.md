# Photo Questions

A student photographs a homework question instead of typing it.

**Disabled by default.** Set `PHOTO_QUESTIONS_ENABLED=1` to turn it on, and read [Before enabling it](#before-enabling-it) first.

---

## The one design rule

**The photo is an input method, not an answer source.**

```
image → moderate → transcribe to text → discard image → existing pipeline
```

The tutoring model never sees the picture. It receives exactly the text it would have received had the student typed the question, which means retrieval, grounding states, citations, the not-in-your-textbook refusal, output moderation and response validation all continue to work unchanged.

The alternative — sending the image and the retrieved chunks to a vision model and letting it answer — was rejected. It bypasses every one of those guarantees, and "GuruJi read it off your homework sheet" is precisely the ungrounded answer this product exists not to give.

---

## What is stored

| | Stored? |
|---|---|
| The image | **No.** Held in memory for one request, then dropped. It stays visible in the chat for the rest of the browser session only — see [Why the photo disappears on reload](#why-the-photo-disappears-on-reload). |
| The transcribed question | Yes, as the student's message, marked `source='photo'`. |
| A blocked photo | Only an event: `moderation_flags` records `[photo question, blocked]`, not the picture. |

There is no object store, no disk write, no Postgres bytea. The DPDP reasoning is the same one already recorded against `students.avatar` in `schema.sql`: a photograph taken by a child is personal data of the child and of anyone else in frame, and the lawful basis for retaining it does not exist. Not retaining it means there is no basis to need.

`messages.source` exists because the image doesn't. It is the only signal, in a transcript read weeks later by a founder or a parent, that the child sent a picture rather than typed.

---

## Request path

Ordered cheapest-first, so nothing free happens after something paid:

| Step | Fails with |
|---|---|
| 1. Feature flag off | `404` — an endpoint that is switched off should not advertise that it exists |
| 2. Not a student | `403` |
| 3. Rate limited | `429` |
| 4. Over `MAX_IMAGE_BYTES` | `413` — read with a ceiling, not trusting `Content-Length` |
| 5. Wrong MIME type | `422` |
| 6. Magic bytes disagree with MIME | `422` — a declared type is a claim, not evidence |
| 7. **Image moderation flags it** | `422`, flag recorded, **vision never called** |
| 8. Nothing readable in it | `422` with a retake message |
| 9. Transcribed | → the ordinary tutoring pipeline |

Step 7 before step 9 is the important ordering. A camera pointed at homework also catches faces, rooms and siblings. Transcribing first would mean flagged content had already reached a generation model by the time anything noticed.

---

## Client-side downscaling

`frontend/src/photo.ts` resizes to 1000px on the long edge at JPEG quality 0.72 before upload, using canvas — no library.

A phone camera produces 3–8 MB at 4000px. Sending that raw costs a student real money on a metered connection and buys nothing, because the vision call runs at `detail: "low"` and downsamples server-side anyway. 1000px is enough to read printed and neat handwritten text off a page.

The whole feature, downscaling included, cost **590 bytes** of gzipped bundle against the 40 kB budget. An image-resize package would have been 30 kB+ to do what four lines of `drawImage` does.

---

## Body size limits

Three limits sit in front of this endpoint, and all three had to be raised — **on this route only**:

| Layer | Global | Photo route |
|---|---|---|
| Caddy `request_body` | 64 KB | 6 MB |
| nginx `client_max_body_size` | 32 k | 6 m |
| App `MAX_IMAGE_BYTES` | — | **5 MB** |
| Client after downscaling | — | ~0.4–0.9 MB typical |

Each layer must exceed the one below it. The app limit is deliberately the smallest of the three server-side limits, so an oversized upload is rejected by the application — which can explain itself in Hinglish — rather than by a proxy, which can only return a bare 413.

Raising any of them globally would let every endpoint accept a 6 MB body.

---

## Cost

| | Per turn | $5/day cap supports |
|---|---|---|
| Typed question (measured) | $0.0038 | ~260 students |
| Photo, `VISION_DETAIL=low` | ~$0.0054 | ~183 students |
| Photo, `VISION_DETAIL=high` **(default)** | **~$0.0080** | ~125 students |

`detail` is the single biggest lever here, on both cost and quality:

- **`low`** downsamples the image to ~512px and costs a flat ~85 input tokens whatever you send. Fine for printed text, marginal for handwriting — and it makes a larger upload completely pointless, because the model never sees the extra pixels.
- **`high`** reads the image in tiles at full resolution. Roughly 2.6× the cost of `low`, and the only setting where a bigger, sharper photo actually buys better transcription.

`high` is the default because the subject is a child's handwritten homework, which is exactly what `low` reads worst. Set `VISION_DETAIL=low` to halve the cost if your students photograph printed textbook questions rather than their own writing.

[Guessing on the token counts — modelled, not measured. Check `llm_spend` after the first day of real photos.]

Latency is worse too: the vision call is a fifth sequential round trip, on the slowest path. Expect a photo turn to run 1–2s longer than a typed one.

---

## Before enabling it

1. **Parental consent must cover images.** This is the blocker. A consent flow that mentions "questions and answers" does not cover a camera.
2. **Take one photo yourself and read the transcript**, checking that `source='photo'` is set and that the transcription matched what you photographed.
3. **Test a rejection**: photograph something that is not a question, confirm you get the retake message and not a not-in-your-textbook refusal.
4. **Watch `llm_spend`** for the first day. The cost figure above is modelled.
5. **Decide `VISION_DETAIL`** by photographing a page of your own worst handwriting at both settings and comparing the transcriptions.

---

## What is deliberately not built

- **Storing the image**, even temporarily. See above.
- **Persisting the photo across a reload.** See below.
- **Multi-question pages.** The prompt takes the circled or first question. A page selector is a UI project and there is no evidence yet that students need it.
- **Handwriting-heavy transcription tuning.** `detail: "low"` reads neat handwriting; messy handwriting will misread. The `transcribed_text` shown back to the student is the mitigation — they can see the misread and retype.
- **Attach a file.** A student on a shared phone has no documents. All of the risk, none of the demand. The menu row was removed rather than scheduled.


---

## Why the photo disappears on reload

Within a session, the picture stays in the chat above its transcription. Both are
there on purpose: the photo is what the child sent, the text is what GuruJi
understood, and seeing them together is the only way to tell a misread of their
handwriting from a wrong answer.

The preview is a browser object URL over the file the student chose. It lives in
that tab, and nothing about it is ever uploaded beyond the one request that gets
transcribed. Reload, and the transcript is rebuilt from the server, which has
only the text — so the image is gone and a `Sent as a photo` marker takes its
place, driven by `messages.source`.

That is the feature working, not a gap in it. Making the picture survive a reload
means storing a photograph taken by a child: an object store, a retention policy,
an access-control story, a deletion path, and a lawful basis under the DPDP Rules
for holding an image of a minor and of whoever else was in frame. Every one of
those is a real piece of work, and none of them is needed to answer the question.

Object URLs are revoked when the chat unmounts. They are not garbage collected
otherwise, and twenty photos in a session would otherwise pin twenty full-size
images in memory on a phone that has little to spare.
