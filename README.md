# DarkFiles automation starter

This starter creates one English-language, vertical YouTube Shorts **draft** per scheduled run.
It uses Gemini for a short script, gTTS for narration, Pillow for simple original title cards,
and FFmpeg to render an MP4.

## Setup
1. Add `GEMINI_API_KEY` under Repository Settings -> Secrets and variables -> Actions.
2. Add these files to the repository, preserving the `.github/workflows/` folder.
3. Open Actions, enable workflows if prompted, select "DarkFiles daily video draft", and click "Run workflow".
4. Download the generated MP4 from the run's Artifacts section.

## Important
- This workflow does NOT upload or publish to YouTube. Review facts, sources, narration, and video first.
- The visual treatment is intentionally simple text-card storytelling, not stock footage.
- gTTS and Gemini free-tier availability, quotas, and terms can change.
- GitHub scheduled workflows are not a guaranteed always-on service. Check failed runs and repository activity.
- Do not commit API keys or OAuth credentials to the repository.
