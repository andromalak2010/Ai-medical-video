---
name: "medical-motion-video"
description: "Make a full faceless 9:16 medical short (30-60s) in Egyptian Arabic for Dr.Andro Malak's page: script, voice, clean 3D medical shots via Higgsfield, motion text, end card. Use for any medical video/reel/short request."
---

# Medical Motion Video (faceless, Egyptian Arabic)

The user is a medical intern (امتياز) building a faceless medical page. He never appears on camera and never uses his own voice. Every video should feel like the same channel: same 3D look, same narrator, same text style, same end card. Consistency is what makes a page recognizable, so don't reinvent the style per video unless he asks.

Talk to him in Egyptian Arabic.

## Fixed channel identity (defaults)

- Format: vertical 9:16, 30-60 seconds.
- Visual style: clean 3D medical. Soft studio lighting, white / light-teal / soft-blue palette, glossy but not gory anatomy, simple stylized human figures, shallow depth of field. Think hospital-education animation, not horror.
- Shots: change every 3-4 seconds so viewers don't get bored. All visuals are generated, never taken from other sites.
- Narrator: generated voice, warm Egyptian colloquial tone. Default preset voice: Holden (his earlier pick). Ask only if he wants a change.
- Text: big bold Arabic, max ~6 words per card, RTL, inside the 9:16 safe zone (keep away from the top 12% and bottom 20% where app UI sits).
- End card (every video, exactly this spelling): `Dr.Andro Malak` + a short follow call (تابعني عشان تعرف أكتر).

## Workflow

### 1. Lock the topic and check the medicine
Get the topic in one line. Then verify the medical content against a current guideline before writing (search the web: AHA / ERC / WHO / NICE / UpToDate-style sources, Egyptian MoH if relevant). Facts change and a wrong first-aid step on a public page can hurt someone. Note the source for him in one line.

Never put real patient details in a video (names, faces, files, photos from the hospital). If he describes a case he saw, turn it into an anonymized, generic scenario.

### 2. Script (Egyptian Arabic)
Structure:
1. **Hook (3-6 s)**: a dramatic moment or a surprising question. e.g. a person touches a live wire and gets thrown back.
2. **Core (20-45 s)**: 3-5 numbered steps or key facts. One idea per sentence.
3. **Myth / common mistake (optional, 5 s)**: "غلط شائع: ..." — this drives comments and shares.
4. **Red flag line**: when to call 123 / go to the ER.
5. **End card (3 s)**: Dr.Andro Malak.

Pacing: about 2.5 Egyptian-Arabic words per second, so 40 s ≈ 100 words. Write it the way people talk ("لو لقيت حد...", "أول حاجة تعملها..."), not fusha. Keep medical terms simple; add the English term in on-screen text only if useful.

Show him the script as a table: `# | time | narration | on-screen text | visual`. Wait for his OK or edits before spending any credits — regenerating video is expensive, rewriting text is free.

### 3. Shot list and prompts
One shot per 3-4 s of script. For each shot write an English prompt with this template:

`Clean 3D medical animation, [subject + action], soft studio lighting, white and light teal palette, stylized human figure, shallow depth of field, vertical 9:16, smooth slow camera [push-in / orbit / pan], no text, no logos, no gore`

Always keep `no text` in the prompt — the AI writes broken Arabic; all text is added later as motion graphics. Keep the same character description across shots (e.g. "young man in grey t-shirt") so it feels like one story.

For anatomy shots (heart, lungs, nerves), prefer translucent glowing organs inside a stylized body rather than realistic dissection.

### 4. Generate with Higgsfield
- Check balance and estimate the cost of the whole batch first (`balance`, `estimate_video_cost` / `estimate_image_cost`). Tell him the total and get a yes before generating. Credits are his money.
- Use `models_explore` if unsure which model fits; pick one good 3D-capable video model and keep it for the whole video for a consistent look.
- Usually: generate a key image per shot (`generate_image_batch`) → animate it (`generate_video_batch`, 3-5 s each). Image-first keeps the look consistent and lets him reject a frame cheaply.
- Narration: generate audio with the chosen voice from the approved script (`generate_audio`). Generate the voice before final cuts so shot lengths can follow the speech.
- Wait with `jobs_wait`; don't re-call a generation to "retry" — that charges again. If a shot is bad, regenerate only that shot.

### 5. Edit and motion text
Assemble in Higgsfield's editing room (`prepare_editing_room` / `editing_room_*`, then `export_editing_room`):
- Cut shots to the narration.
- Add the on-screen text cards from the script: pop/slide-in animation, bold white text with a soft dark shadow or a teal pill background, numbers big (١ ٢ ٣ or 1 2 3 — stay consistent).
- Burned-in captions of the narration help since many watch muted.
- Light background music, low under the voice; a whoosh/zap SFX on the hook if it fits.
- End card: Dr.Andro Malak, 3 s.

If the editing room can't do a specific text effect, say so in one line and use the closest option rather than stopping.

### 6. Deliver
Give him:
- the final video,
- a ready post caption in Egyptian Arabic (hook line + 3 key points + call to follow + 5-8 hashtags mixing Arabic and English, e.g. #اسعافات_اولية #FirstAid),
- the source used for the medical info.

Then one short line on what could be better next time, if anything.

## Example
Input: "اعمل فيديو عن إسعاف حد بيتشرق"
- Hook: a man at dinner suddenly grabs his throat, can't speak.
- Steps: اسأله "انت بتتشرق؟" → لو مش قادر يتكلم أو يكح: ٥ خبطات على الضهر → ٥ ضغطات بطن (Heimlich) → كرر → لو أغمى عليه: اطلب ١٢٣ وابدأ CPR.
- Myth: "غلط شائع: تديله مية يشربها".
- End card: Dr.Andro Malak.