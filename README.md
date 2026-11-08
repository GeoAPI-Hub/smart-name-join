# Smart Name Join: a QGIS plugin

**Join a table to a layer by name, even when the names are spelled differently.**

| Your table says | Your layer says |
|---|---|
| Ooty | Udhagamandalam |
| Arni | Arani |
| Thoothukkudi | Thoothukudi |
| Chengalpattu district | Chengalpattu |
| Kanniyākumāri | Kanyakumari |

A normal QGIS join needs identical text. It quietly drops rows like these. When two villages share a name, it joins to whichever one it finds first, and you get no warning.

On 283 real Tamil Nadu towns joined to 20,620 Overture Maps places:

| Method | Right | Wrong | For review |
|---|---|---|---|
| Normal exact join | 136 | 31 | (116 missing) |
| Smart Name Join, **no AI** (free, offline, ~3 s) | 254 | 0 | 29 |
| Smart Name Join + Claude on the 29 hard rows | 279 | 0 | 2 (+2 "no match") |
| Smart Name Join + local qwen3:4b (Ollama) | 261 | 2 | 20 |

The answer key is shared Wikidata IDs, so it doesn't depend on how a name is spelled.

## Install

1. Download `smart_name_join.zip` from the [latest release](../../releases/latest).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**. Pick the zip, then **Install Plugin**.
3. Open it from **Vector → Smart Name Join**, the toolbar button, or the Processing Toolbox (search for "smart name").

It needs QGIS 3.28 or newer, including QGIS 4, and no extra Python packages.

## Try it on the sample data

`sample_data/` (also attached to the release) has the files from the video:

- `town_population.csv`: the table. Fields: town, district, population.
- `tn_places.gpkg`: the layer. Fields: name, district, class.
- `tn_districts.gpkg`: district outlines, for the background.

Settings: table name field `town`, table group `district`, table info `population`. Layer name field `name`, layer group `district`, layer info `class`. Start with **No AI**.

## AI options (optional, only for the rows the rules can't settle)

| Provider | What you need |
|---|---|
| No AI | nothing (the default) |
| Ollama | [Ollama](https://ollama.com) running locally, e.g. `ollama pull qwen3:4b` |
| OpenAI-compatible | URL + model; the key goes in a QGIS authentication config (Basic, key in *password*) or `OPENAI_API_KEY` |
| Claude API | key in an authentication config or `ANTHROPIC_API_KEY` (default model `claude-opus-5-5`) |
| Claude Code CLI | the `claude` command installed and signed in; no key needed |

The AI never invents a match. It gets a numbered list of **real features from your layer** and can only pick one of them or answer "none".

The plugin also sends a row to review instead of trusting the AI in these cases:

- two candidates are identical;
- the AI's reason names a different town than its answer;
- the AI's confidence is low.

Answers are cached in your QGIS profile, so a re-run costs nothing.

Status of each provider:

- **Tested:** No AI, Ollama (qwen3:4b), Claude Code CLI.
- **Written but not yet tested:** Claude API and OpenAI-compatible API. Please open an issue if you try them.

## Output fields

| field | meaning |
|---|---|
| `match_status` | `matched`, `review` (a human should check it) or `none` |
| `match_method` | exact, normalized, phonetic, fuzzy, ambiguous, other_group, ai, ai_no_match |
| `match_score` | 0–1 text similarity |
| `ai_confidence` | 0–1, when the AI decided |
| `match_reason` | why it was matched, or why it wasn't |
| `matched_name`, `matched_group` | what it joined to |
| `alternatives` | other close candidates |

Filter on `match_status = 'review'` and check those rows by hand.

## How it matches

The steps run in order. A later step only sees rows the earlier steps didn't settle.

1. **exact**: identical text.
2. **normalized**: removes accents, punctuation, brackets and noise words (district, town, panchayat, municipality, …).
3. **phonetic**: a transliteration key for romanised Indian names: th→t, dh→d, kk→k, oo→u, and k/g, t/d, p/b treated as one sound.
4. **fuzzy**: a similarity score. It's accepted only when it's high **and** clearly ahead of the second-best candidate.
5. **group first**: with a group field such as district, the search starts inside the matching group. Group names are matched with the same steps. A clean name hit in a *different* group goes to review, because the table's group might be out of date.
6. **AI** (optional): may suggest other names (old or official names), which are then searched in your layer. It then picks from the candidates.

## Privacy

With an AI provider, only these are sent to that provider: the name, group and info fields of the rows the rules couldn't settle, plus their candidate names. With No AI or Ollama, nothing leaves your computer.

## Data credits (sample data)

Overture Maps Foundation (release 2026-09-23.1), GeoNames (CC BY 4.0), Wikidata (CC0).

## License

GPL-2.0-or-later. Made for the TechStackSpatial YouTube channel's AI x QGIS series.
