# AI Medical Video Production

A faceless social media video system for medical education: short explainer videos made with AI tools, without showing the creator's face or voice.

## Goals

- Create realistic AI-generated medical explainer videos.
- Automate as much of the workflow as possible.
- Use free tools and free plans where possible.
- Work entirely from an iPad, through Claude Code on the web.

## Folder structure

| Folder | What goes here |
|---|---|
| `scripts/` | Automation scripts (added later, once the workflow is tested) |
| `prompts/` | Reusable prompts for AI video, voice, and image tools |
| `videos/` | Finished videos and their notes (large files may be kept elsewhere) |
| `assets/` | Logos, caption styles, background music, and other reusable files |

## Content rules

1. **Educational only.** Videos explain concepts and cases in general terms. They are not medical advice.
2. **No real patient information.** Never use real names, images, records, or identifiable details. Cases must be fully anonymized and changed.
3. **Use reliable sources.** Base every claim on textbooks, guidelines, or peer-reviewed sources. Note the source for each video.
4. **Disclaimer on every video.** Example: "For educational purposes only. Not medical advice. Consult a doctor for medical concerns."
5. **Label AI-generated content.** Use the platform's AI-content label whenever footage looks realistic.
6. **No impersonation.** Do not use the names, logos, or likeness of real doctors, hospitals, or organizations.

## Basic workflow (planned)

1. Choose a topic and write a short script (hook, explanation, key lesson, disclaimer).
2. Generate narration with a text-to-speech tool.
3. Generate or select visuals with AI video and image tools.
4. Add captions and a thumbnail.
5. Export, review, and save the notes in `videos/`.

Each step will be documented here as it is tested.

## Status

Setup stage. The folder structure is in place. Tools and the first test video are still to be chosen.
