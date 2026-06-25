"""The "write in the user's voice" feature.

By default the pipeline writes module prose in a house style: a friendly,
beginner-first (ELI15) explanatory voice. This module lets the user opt into
having that prose written in **their own voice** instead.

Design (per user decisions):

* **Analyze once, reuse.** The user's writing samples are distilled into a
  **Voice Profile** note saved at ``config.get_voice_profile_path()``. Once it
  exists, every future run reuses it without re-analyzing — mirroring the
  "create-once, persistent" philosophy of the per-theme Bases. It is refreshed
  only when the user explicitly asks.
* **Two sample sources.** Samples are read first from the vault folder
  ``config.get_voice_samples_folder()`` (drop a few of your own notes there); if
  that folder is missing or empty, the user is asked to paste 1–3 samples.
* **Voice ≠ structure.** The profile governs only *how* the prose reads — tone,
  rhythm, diction, point of view. The pipeline's scaffolding (YAML frontmatter,
  the ``*tags:*`` line, tables, SVG diagrams, ELI15 clarity, navigation,
  footnotes) is always produced as specified, whatever voice is chosen.

Two things are exported:

* :func:`voice_step_text` — the "Step 0: Choose the writing voice" block that the
  ``process-youtube`` prompt embeds, with the configured folder/profile paths
  already substituted (the runtime ``{voice}`` token is left for the caller).
* :func:`register_voice_prompt` — a standalone ``analyze-voice`` prompt so the
  user can build or refresh their Voice Profile up front, independent of
  processing any particular video.

Substitution is done with ``str.replace`` (never ``str.format``): these strings
contain literal ``{``/``}`` in YAML/code examples that would break ``.format``.
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from config import get_voice_profile_path, get_voice_samples_folder

# The dimensions to capture when distilling samples into a profile. Kept in one
# place so the embedded step and the standalone prompt stay in sync.
VOICE_DIMENSIONS = """Capture only *how* the user writes, never *what* the samples were about:

- **Tone & persona** — formal vs casual, warm vs dry, playful vs serious; how much humor, and what kind.
- **Sentence rhythm** — typical length, how much it varies, short punchy lines vs long flowing ones, use of fragments.
- **Vocabulary & diction** — plain vs technical, slang or regional words, any pet/favorite words and phrases.
- **Point of view** — first person ("I"), direct address ("you"), collective ("we"), or detached third person.
- **Prose habits** — rhetorical questions, analogies, asides/parentheticals, em dashes, lists, emphasis style.
- **Signature quirks** — recurring openers, transitions, sign-offs, punctuation tics, anything unmistakably theirs."""

# The note written to the profile path. Literal braces below are intentional and
# safe because we only ever ``.replace`` named tokens, never ``.format``.
VOICE_PROFILE_TEMPLATE = """```
---
type: voice-profile
updated: YYYY-MM-DD
samples_analyzed: N
---

# Voice Profile

*How the user writes. Applied to module/overview prose when "my voice" is chosen. Only tone, phrasing, rhythm and word choice follow this profile — structure, frontmatter, tags, tables, SVGs, ELI15 clarity, navigation and footnotes always stay per the pipeline.*

## Tone & Persona
[2-4 sentences]

## Sentence Rhythm
[2-4 sentences]

## Vocabulary & Diction
[2-4 sentences, with example words/phrases they actually use]

## Point of View
[1-2 sentences]

## Signature Quirks
- [quirk]
- [quirk]

## Do
- [concrete instruction, e.g. "open sections with a short, punchy one-liner"]
- [concrete instruction]

## Don't
- [concrete instruction, e.g. "avoid corporate buzzwords like 'leverage' / 'synergy'"]
- [concrete instruction]
```"""


def _apply_paths(text: str) -> str:
    """Substitute the configured folder/profile paths into a template string."""
    return text.replace("{voice_samples_folder}", get_voice_samples_folder()).replace(
        "{voice_profile_path}", get_voice_profile_path()
    )


# ---- The "Step 0" block embedded by the process-youtube prompt ---------------

_VOICE_STEP = """## Step 0: Choose the writing voice

By default, prose is written in the pipeline's **house style** — a friendly,
beginner-first (ELI15) explanatory voice. The user can instead have it written
in **their own voice**.

**Resolve the voice preference now.** The `voice` argument is "{voice}".
- If it is `default`, use the house style and skip the rest of this step.
- If it is `mine`, write in the user's voice — resolve the profile below.
- If it is blank/unset, **ask the user once:** "Would you like these notes
  written in your own voice, or in the default explanatory house style?" If they
  choose the default, skip the rest of this step.

**If writing in the user's voice, resolve the Voice Profile (analyze once, reuse):**

1. **Look for a saved profile** at `{voice_profile_path}` with `read_note`. If it
   exists, THAT is the user's voice — read it, apply it to all prose below, and do
   NOT re-analyze. You may offer once: "I found a saved voice profile — use it as
   is, or refresh it from new samples?" Only rebuild if they ask.
2. **If no profile exists, gather writing samples:**
   - First check the samples folder `{voice_samples_folder}` (use `list_folder`,
     then `read_note` on each file). Use whatever writing is there.
   - If that folder is missing or empty, **ask the user to paste 1-3 samples** of
     their own writing (a few paragraphs each is plenty).
   - If no samples can be obtained at all, tell the user and fall back to the
     house style.
3. **Analyze the samples into a Voice Profile.** {voice_dimensions}
4. **Save the profile** to `{voice_profile_path}` with `create_note` using the
   template below, so every future run reuses it.

Voice Profile note (save at `{voice_profile_path}`):

{voice_profile_template}

**Applying the voice (whatever the source):**
- It governs the **tone, phrasing, sentence rhythm, and word choice** of the
  explanatory prose in every module and the overview note.
- It does NOT change the pipeline's structure: YAML frontmatter, the `*tags:*`
  line, tables for structured data, SVG diagrams, ELI15 clarity (still define
  jargon inline), navigation, and footnotes are ALWAYS produced as specified.
- Still never copy-paste from the transcript — rewrite it in the user's voice."""


def voice_step_text() -> str:
    """Return the 'Step 0' block for embedding in the process-youtube prompt.

    The configured samples-folder and profile paths, the analysis dimensions, and
    the profile template are substituted in. The runtime ``{voice}`` token is left
    untouched for the process-youtube registrar to fill from its own argument.
    """
    text = _VOICE_STEP.replace("{voice_dimensions}", VOICE_DIMENSIONS)
    text = text.replace("{voice_profile_template}", VOICE_PROFILE_TEMPLATE)
    return _apply_paths(text)


# ---- The standalone analyze-voice prompt ------------------------------------

_ANALYZE_VOICE_PROMPT = """Build (or refresh) the user's **Voice Profile** so the
pipeline can write notes in their own voice.

Follow these steps:

## Step 1: Gather writing samples

{pasted_block}Check the samples folder `{voice_samples_folder}` with `list_folder`,
then `read_note` each file to collect the user's own writing. Combine that with any
pasted samples above.

If there are no samples anywhere, ask the user to paste 1-3 samples (a few
paragraphs each) and stop until they do.

## Step 2: Check for an existing profile

Call `read_note` on `{voice_profile_path}`. If it already exists, tell the user you
are about to refresh it (you will overwrite it in Step 4).

## Step 3: Analyze the voice

{voice_dimensions}

## Step 4: Save the profile

Write the profile to `{voice_profile_path}` with `create_note` (pass
`overwrite=true` if refreshing an existing one), using this template:

{voice_profile_template}

Then confirm to the user that future `process-youtube` runs will reuse this profile
when they choose "my voice", and that they can refresh it any time by running this
prompt again."""


def register_voice_prompt(mcp: FastMCP) -> None:
    """Register the standalone ``analyze-voice`` prompt on the FastMCP instance."""

    @mcp.prompt(
        name="analyze-voice",
        description=(
            "Analyze samples of the user's own writing into a reusable Voice "
            "Profile, so process-youtube can write notes in their voice. Reads "
            "samples from the configured folder and/or pasted text, then saves the "
            "profile for reuse."
        ),
    )
    def analyze_voice(
        samples: Annotated[
            str,
            Field(
                description=(
                    "Optional pasted writing samples. If omitted, samples are read "
                    "from the vault's Voice Samples folder."
                )
            ),
        ] = "",
    ) -> str:
        pasted_block = ""
        if samples.strip():
            pasted_block = (
                "The user pasted these writing samples:\n\n"
                "<<<SAMPLES\n" + samples.strip() + "\nSAMPLES\n\n"
            )
        text = _ANALYZE_VOICE_PROMPT.replace("{pasted_block}", pasted_block)
        text = text.replace("{voice_dimensions}", VOICE_DIMENSIONS)
        text = text.replace("{voice_profile_template}", VOICE_PROFILE_TEMPLATE)
        return _apply_paths(text)
