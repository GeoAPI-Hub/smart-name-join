# Smart Name Join (QGIS plugin)

Join a table to a layer **by name** when the spellings don't match.

| Your table says | Your layer says |
|---|---|
| Tiruvallur | Thiruvallur |
| Ooty | Udhagamandalam |
| Chengalpattu district | Chengalpattu |
| Kanniyākumāri | Kanyakumari |

A normal QGIS join needs identical text. It drops these rows, and when two villages share a
name it silently joins to the wrong one.

## Install (2 minutes)

1. Download `smart_name_join.zip`.
2. QGIS → **Plugins → Manage and Install Plugins → Install from ZIP** → pick the zip → **Install Plugin**.
3. Open it from **Vector → Smart Name Join**, the toolbar button, or the Processing Toolbox (search "smart name").

QGIS 3.28 or newer, including QGIS 4. You don't need any extra Python packages.

## Use

1. **Table to join**: your spreadsheet or CSV (it can have no geometry), plus its name field.
2. **Layer to join to**: your map layer, plus its name field.
3. *Optional:* a group field on both sides, e.g. district. It's faster and safer, and the district names are matched the same way.
4. *Optional:* info fields (population, type) that the AI can use to break ties.
5. **AI for the hard rows**: leave it on *No AI* to run free and offline, or pick one:
   - **Ollama**: local and free. Install Ollama, run `ollama pull qwen3:4b`, then leave the model blank or type yours.
   - **OpenAI-compatible**: OpenAI, OpenRouter, LM Studio and others. Set the URL and model, and put the key in a QGIS
     authentication config (Basic: key in the *password* box) or in the env var `OPENAI_API_KEY`.
   - **Claude API**: the key goes in the authentication config or `ANTHROPIC_API_KEY`. The default model is `claude-opus-5-5`.
   - **Claude Code CLI**: uses your Claude plan through the `claude` command. No key needed.
6. Run it. The result layer has every table row plus:

| field | meaning |
|---|---|
| `match_status` | `matched`, `review` (a human should check it) or `none` |
| `match_method` | exact, normalized, phonetic, fuzzy, ambiguous, other_group, ai, ai_no_match |
| `match_score` | 0–1 text similarity |
| `ai_confidence` | 0–1, only when the AI decided |
| `match_reason` | why the row was matched (or why it wasn't) |
| `matched_name`, `matched_group` | what it joined to |
| `alternatives` | other close candidates, for review |

Filter `match_status = 'review'` and check those rows by hand.

## How it decides

1. **exact**: identical text.
2. **normalized**: the same text after removing accents, punctuation, brackets and noise words
   (district, town, panchayat, municipality ...).
3. **phonetic**: the same transliteration key (th→t, dh→d, kk→k, oo→u, g/k, d/t ...), for romanised Indian names.
4. **fuzzy**: a similarity score, accepted only when it's high **and** clearly better than the next candidate.
5. **AI** (optional, only for rows still undecided): the AI can suggest other names (old or official), and those
   are searched in **your** layer. Then it picks from a numbered list of **real** features, or answers "no match".
   It can't invent a feature. If two candidates look identical, the row goes to review instead of becoming a guess.

AI answers are cached in your QGIS profile folder (`smart_name_join/ai_cache.json`), so a re-run costs nothing.

## Privacy

With an AI provider, the names, groups and info fields of the *undecided* rows, plus their candidate names, are sent to
that provider. Nothing else from your data is sent. With *No AI* or Ollama, nothing leaves your computer.

License: GPL-2.0-or-later.
